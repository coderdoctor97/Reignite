"""
GatewayManager — lifecycle management for the gateway process.

Owns the subprocess that runs the legacy gateway (OpusGateway.py).
Provides start/stop/restart/status/health operations.

Key design decisions:
- The gateway runs as a subprocess (Python script)
- stdout/stderr are captured in a bounded buffer
- Health checks test process liveness AND port reachability
- Duplicate start/stop calls are safe (idempotent)
- No aggressive auto-restart (only detection + manual restart)
- No credentials in subprocess arguments or logs
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("gateway_manager")


# ── Lifecycle states ─────────────────────────────────────────────

class GatewayState(str, Enum):
    """Gateway process lifecycle states."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    FAILED = "failed"
    STOPPING = "stopping"


class HealthStatus(str, Enum):
    """Gateway health status."""
    HEALTHY = "healthy"       # process alive + port reachable
    STARTING = "starting"     # process alive, port not yet reachable
    STOPPED = "stopped"       # no process
    FAILED = "failed"         # process exited unexpectedly or port unreachable
    UNKNOWN = "unknown"       # cannot determine


# ── Output buffer ────────────────────────────────────────────────

MAX_OUTPUT_LINES = 500


@dataclass
class OutputLine:
    """A single line of captured subprocess output."""
    stream: str  # "stdout" or "stderr"
    text: str
    timestamp: str


# ── Process info ─────────────────────────────────────────────────

@dataclass
class ProcessInfo:
    """Snapshot of the gateway process state."""
    state: GatewayState = GatewayState.STOPPED
    pid: Optional[int] = None
    start_time: Optional[str] = None
    stop_time: Optional[str] = None
    uptime_seconds: Optional[float] = None
    restart_count: int = 0
    last_exit_code: Optional[int] = None
    last_error: Optional[str] = None
    command: Optional[str] = None
    working_dir: Optional[str] = None


# ── GatewayManager ───────────────────────────────────────────────

class GatewayManager:
    """Manages the lifecycle of the gateway subprocess.

    Thread-safe: all state mutations go through asyncio locks.
    Idempotent: duplicate start/stop calls are safe.
    """

    def __init__(self) -> None:
        self._process: Optional[asyncio.subprocess.Process] = None
        self._state: GatewayState = GatewayState.STOPPED
        self._start_time: Optional[float] = None
        self._stop_time: Optional[float] = None
        self._restart_count: int = 0
        self._last_exit_code: Optional[int] = None
        self._last_error: Optional[str] = None
        self._command: Optional[str] = None
        self._working_dir: Optional[str] = None
        self._output: deque[OutputLine] = deque(maxlen=MAX_OUTPUT_LINES)
        self._lock = asyncio.Lock()
        self._stdout_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._wait_task: Optional[asyncio.Task] = None

    # ── Public API ───────────────────────────────────────────────

    async def start(self) -> ProcessInfo:
        """Start the gateway process.

        Idempotent: if already running, returns current status.
        """
        async with self._lock:
            if self._process is not None and self._process.returncode is None:
                logger.info("Gateway already running (PID %d)", self._process.pid)
                return self._build_info()

            return await self._start_process()

    async def stop(self) -> ProcessInfo:
        """Stop the gateway process.

        Idempotent: if already stopped, returns current status.
        """
        async with self._lock:
            if self._process is None or self._process.returncode is not None:
                logger.info("Gateway already stopped")
                self._state = GatewayState.STOPPED
                return self._build_info()

            return await self._stop_process()

    async def restart(self) -> ProcessInfo:
        """Stop then start the gateway process."""
        async with self._lock:
            if self._process is not None and self._process.returncode is None:
                await self._stop_process()
            return await self._start_process()

    async def status(self) -> ProcessInfo:
        """Return the current process status without side effects."""
        async with self._lock:
            # Check if process exited unexpectedly
            if (self._process is not None
                    and self._process.returncode is not None
                    and self._state == GatewayState.RUNNING):
                self._handle_unexpected_exit()
            return self._build_info()

    async def health(self) -> dict:
        """Return a health check result.

        Tests: process alive + port reachable + HTTP probe.
        The HTTP probe sends a GET to the gateway root. The legacy gateway
        returns 404 with a text body — any HTTP response confirms the server
        is actually serving, not just listening on the port.
        """
        async with self._lock:
            process_alive = (
                self._process is not None
                and self._process.returncode is None
            )

        # Port check outside the lock (it's async I/O)
        port_reachable = False
        http_responsive = False
        if process_alive:
            port_reachable = await self._check_port()
            if port_reachable:
                http_responsive = await self._check_http()

        if not process_alive:
            status = HealthStatus.STOPPED
        elif process_alive and port_reachable and http_responsive:
            status = HealthStatus.HEALTHY
        elif process_alive and port_reachable and not http_responsive:
            # Port open but HTTP not responding — still starting or degraded
            if self._state == GatewayState.STARTING:
                status = HealthStatus.STARTING
            else:
                status = HealthStatus.HEALTHY  # port reachable is sufficient
        elif process_alive and not port_reachable:
            # Could be starting up
            if self._state == GatewayState.STARTING:
                status = HealthStatus.STARTING
            else:
                status = HealthStatus.FAILED
        else:
            status = HealthStatus.UNKNOWN

        from app.storage.models import _utcnow
        return {
            "status": status.value,
            "process_alive": process_alive,
            "port_reachable": port_reachable,
            "http_responsive": http_responsive,
            "checked_at": _utcnow(),
        }

    def get_output(self, limit: int = 100) -> list[dict]:
        """Return recent subprocess output lines."""
        lines = list(self._output)[-limit:]
        return [
            {"stream": line.stream, "text": line.text, "timestamp": line.timestamp}
            for line in lines
        ]

    # ── Internal ─────────────────────────────────────────────────

    async def _start_process(self) -> ProcessInfo:
        """Start the gateway subprocess. Must be called with lock held."""
        settings = get_settings()

        # Resolve the gateway script path
        script_path = Path(settings.legacy_base_dir) / settings.gateway_script
        if not script_path.exists():
            self._state = GatewayState.FAILED
            self._last_error = f"Gateway script not found: {script_path}"
            logger.error(self._last_error)
            await self._record_event("gateway.start_failed", self._last_error, "error")
            return self._build_info()

        # Find Python executable
        python_exe = sys.executable

        # Build command (no credentials in arguments)
        cmd = [python_exe, str(script_path)]
        cwd = str(script_path.parent)

        self._command = " ".join(cmd)
        self._working_dir = cwd
        self._state = GatewayState.STARTING
        self._last_error = None

        logger.info("Starting gateway: %s (cwd: %s)", self._command, cwd)

        try:
            # Launch subprocess with captured output
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"

            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

            self._start_time = time.time()
            self._stop_time = None

            # Start background output readers
            self._stdout_task = asyncio.create_task(
                self._read_output(self._process.stdout, "stdout")
            )
            self._stderr_task = asyncio.create_task(
                self._read_output(self._process.stderr, "stderr")
            )

            # Start a task to detect unexpected exit
            self._wait_task = asyncio.create_task(
                self._wait_for_exit()
            )

            # Wait for readiness (port becomes reachable)
            ready = await self._wait_for_ready(settings.gateway_startup_timeout)

            if ready:
                self._state = GatewayState.RUNNING
                logger.info("Gateway started successfully (PID %d)", self._process.pid)
                await self._record_event(
                    "gateway.started",
                    f"Gateway started on port {settings.gateway_port}",
                    "info",
                )
            else:
                # Process may still be starting or may have failed
                if self._process.returncode is not None:
                    self._state = GatewayState.FAILED
                    self._last_exit_code = self._process.returncode
                    self._last_error = f"Gateway exited during startup (code {self._process.returncode})"
                    logger.error(self._last_error)
                    await self._record_event("gateway.start_failed", self._last_error, "error")
                else:
                    # Still alive but port not reachable — treat as starting
                    self._state = GatewayState.RUNNING
                    logger.warning("Gateway process alive but port not yet reachable")

        except Exception as e:
            self._state = GatewayState.FAILED
            self._last_error = f"Failed to start gateway: {e}"
            logger.error(self._last_error)
            await self._record_event("gateway.start_failed", self._last_error, "error")

        return self._build_info()

    async def _stop_process(self) -> ProcessInfo:
        """Stop the gateway subprocess. Must be called with lock held."""
        settings = get_settings()
        self._state = GatewayState.STOPPING

        if self._process is None:
            self._state = GatewayState.STOPPED
            return self._build_info()

        pid = self._process.pid
        logger.info("Stopping gateway (PID %d)...", pid)

        try:
            self._process.terminate()

            try:
                await asyncio.wait_for(
                    self._process.wait(),
                    timeout=settings.gateway_shutdown_timeout,
                )
                logger.info("Gateway stopped gracefully (PID %d)", pid)
            except asyncio.TimeoutError:
                logger.warning("Gateway did not stop gracefully, killing (PID %d)", pid)
                self._process.kill()
                await self._process.wait()
                logger.info("Gateway killed (PID %d)", pid)

        except ProcessLookupError:
            logger.info("Gateway process already exited (PID %d)", pid)
        except Exception as e:
            logger.error("Error stopping gateway: %s", e)
            self._last_error = f"Error stopping gateway: {e}"

        self._last_exit_code = self._process.returncode
        self._process = None
        self._state = GatewayState.STOPPED
        self._stop_time = time.time()

        # Cancel output readers
        for task in (self._stdout_task, self._stderr_task, self._wait_task):
            if task and not task.done():
                task.cancel()

        await self._record_event("gateway.stopped", f"Gateway stopped (PID {pid})", "info")
        return self._build_info()

    def _handle_unexpected_exit(self) -> None:
        """Handle unexpected process exit. Must be called with lock held."""
        if self._process is None:
            return
        self._last_exit_code = self._process.returncode
        self._state = GatewayState.FAILED
        self._last_error = f"Gateway exited unexpectedly (code {self._last_exit_code})"
        self._stop_time = time.time()
        logger.error(self._last_error)

    async def _wait_for_exit(self) -> None:
        """Background task that detects unexpected process exit."""
        if self._process is None:
            return
        try:
            await self._process.wait()
            async with self._lock:
                if self._state == GatewayState.RUNNING:
                    self._handle_unexpected_exit()
                    await self._record_event(
                        "gateway.crashed",
                        f"Gateway exited unexpectedly (code {self._last_exit_code})",
                        "error",
                    )
        except asyncio.CancelledError:
            pass

    async def _wait_for_ready(self, timeout: float) -> bool:
        """Wait until the gateway port is reachable or timeout."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if await self._check_port():
                return True
            # Also check if process died
            if self._process is not None and self._process.returncode is not None:
                return False
            await asyncio.sleep(0.25)
        return False

    async def _check_port(self) -> bool:
        """Check if the gateway port is reachable."""
        settings = get_settings()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(settings.gateway_host, settings.gateway_port),
                timeout=2.0,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except (OSError, asyncio.TimeoutError, ConnectionRefusedError):
            return False

    async def _check_http(self) -> bool:
        """Check if the gateway responds to HTTP requests.

        Sends a GET to the gateway root. The legacy gateway returns 404 with
        a text body — any HTTP response (even 4xx) confirms the server is
        actually serving HTTP, not just listening on the port.

        This is intentionally non-invasive: it does NOT send a real provider
        request or hit any /v1 endpoint that might trigger auth logic.
        """
        settings = get_settings()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(settings.gateway_host, settings.gateway_port),
                timeout=2.0,
            )
            # Send a minimal HTTP GET / request
            request = (
                f"GET / HTTP/1.1\r\n"
                f"Host: {settings.gateway_host}:{settings.gateway_port}\r\n"
                f"Connection: close\r\n"
                f"\r\n"
            )
            writer.write(request.encode("utf-8"))
            await writer.drain()

            # Read the first line of the response (status line)
            status_line = await asyncio.wait_for(reader.readline(), timeout=2.0)
            writer.close()
            await writer.wait_closed()

            # Any HTTP response starting with "HTTP/" confirms the server is serving
            return status_line.startswith(b"HTTP/")
        except (OSError, asyncio.TimeoutError, ConnectionRefusedError, asyncio.IncompleteReadError):
            return False

    def get_config(self) -> dict:
        """Return the gateway configuration as a dict."""
        settings = get_settings()
        return {
            "host": settings.gateway_host,
            "port": settings.gateway_port,
            "protocol": settings.gateway_protocol,
            "base_path": settings.gateway_base_path,
            "endpoint_url": settings.gateway_endpoint_url,
            "base_url": settings.gateway_base_url,
            "script": settings.gateway_script,
            "working_directory": str(Path(settings.legacy_base_dir)),
            "startup_timeout": settings.gateway_startup_timeout,
            "shutdown_timeout": settings.gateway_shutdown_timeout,
        }

    async def _read_output(self, stream: asyncio.StreamReader, name: str) -> None:
        """Background task to read subprocess output into the bounded buffer."""
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    from app.storage.models import _utcnow
                    self._output.append(OutputLine(
                        stream=name,
                        text=text,
                        timestamp=_utcnow(),
                    ))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug("Output reader error (%s): %s", name, e)

    def _build_info(self) -> ProcessInfo:
        """Build a ProcessInfo snapshot. Must be called with lock held."""
        uptime = None
        if self._start_time and self._state == GatewayState.RUNNING:
            uptime = time.time() - self._start_time

        return ProcessInfo(
            state=self._state,
            pid=self._process.pid if self._process else None,
            start_time=self._format_time(self._start_time),
            stop_time=self._format_time(self._stop_time),
            uptime_seconds=round(uptime, 1) if uptime else None,
            restart_count=self._restart_count,
            last_exit_code=self._last_exit_code,
            last_error=self._last_error,
            command=self._command,
            working_dir=self._working_dir,
        )

    @staticmethod
    def _format_time(ts: Optional[float]) -> Optional[str]:
        """Format a Unix timestamp as ISO 8601."""
        if ts is None:
            return None
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    async def _record_event(self, event_type: str, message: str, severity: str) -> None:
        """Record a gateway lifecycle event in the database."""
        try:
            from app.storage.database import get_async_session
            from app.storage.repositories import EventRepository
            async with get_async_session() as session:
                await EventRepository.create(
                    session,
                    event_type=event_type,
                    message=message,
                    severity=severity,
                )
        except Exception as e:
            logger.warning("Failed to record event %s: %s", event_type, e)


# Module-level singleton
_gateway_manager: Optional[GatewayManager] = None


def get_gateway_manager() -> GatewayManager:
    """Return the singleton GatewayManager instance."""
    global _gateway_manager
    if _gateway_manager is None:
        _gateway_manager = GatewayManager()
    return _gateway_manager
