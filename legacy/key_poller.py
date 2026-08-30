#!/usr/bin/env python3
"""
key_poller.py
─────────────
Thread-safe wrapper around pull_latest_key.py so it can run from the
Opus Control Panel on a 5-second cadence without blocking the GUI.

Design notes:
  • All HTTP / file I/O happens on a worker thread (Tk is not safe to call
    from the worker thread, so the worker only mutates thread-safe state).
  • The control panel reads state via .snapshot() from the Tk thread.
  • The original pull_latest_key.py is invoked via runpy in a fresh
    subprocess only when the user enables polling — we don't import it
    directly to keep its standalone CLI behavior intact.
  • The polling interval is fixed at 5 seconds per the user's request.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Optional

# 5-second polling interval, as requested.
POLL_INTERVAL_SEC = 5.0

# Reasonable timeouts for the subprocess invocation so a hung HTTP
# request doesn't stall the whole worker.
SCRIPT_TIMEOUT_SEC = 20


@dataclass
class PollerSnapshot:
    """Thread-safe view of the poller's current state.

    Created once and mutated under _lock. Tk reads .copy() from the UI thread.
    """
    enabled: bool = False
    running: bool = False
    last_started_ts: float = 0.0
    last_finished_ts: float = 0.0
    last_success: bool = False
    last_error: str = ""
    last_key_prefix: str = ""
    tick_count: int = 0
    success_count: int = 0
    error_count: int = 0
    next_tick_at: float = 0.0  # when running, the time the next poll will fire
    base_dir: str = ""

    def copy(self) -> "PollerSnapshot":
        return PollerSnapshot(
            enabled=self.enabled,
            running=self.running,
            last_started_ts=self.last_started_ts,
            last_finished_ts=self.last_finished_ts,
            last_success=self.last_success,
            last_error=self.last_error,
            last_key_prefix=self.last_key_prefix,
            tick_count=self.tick_count,
            success_count=self.success_count,
            error_count=self.error_count,
            next_tick_at=self.next_tick_at,
            base_dir=self.base_dir,
        )


class KeyPoller:
    """Polls pull_latest_key.py every POLL_INTERVAL_SEC while enabled.

    Lifecycle:
        poller = KeyPoller(base_dir=...)
        poller.start()     # launches worker thread
        poller.stop()      # sets stop flag and joins (with timeout)
        poller.snapshot()  # thread-safe read
    """

    def __init__(self, base_dir: str, log=None, interval: float = POLL_INTERVAL_SEC):
        self._base_dir = base_dir
        self._log = log  # optional callable: log(msg: str, level: str)
        self._interval = float(interval)

        self._state = PollerSnapshot(base_dir=base_dir)
        self._lock = threading.Lock()

        self._stop_event = threading.Event()
        self._enabled_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._worker_started_once = False

    # ------------------------------------------------------------------ public

    @property
    def interval(self) -> float:
        return self._interval

    def start(self):
        """Launch the background worker thread (idempotent)."""
        if self._worker_started_once and self._worker and self._worker.is_alive():
            return
        self._worker_started_once = True
        self._stop_event.clear()
        self._worker = threading.Thread(
            target=self._worker_loop, name="KeyPoller", daemon=True
        )
        self._worker.start()
        self._log_msg("Key poller worker started", "INFO")

    def stop(self, join_timeout: float = 3.0):
        """Signal stop and wait briefly for the worker to exit."""
        self._stop_event.set()
        self._enabled_event.clear()
        with self._lock:
            self._state.enabled = False
            self._state.running = False
            self._state.next_tick_at = 0.0
        if self._worker and self._worker.is_alive():
            try:
                self._worker.join(timeout=join_timeout)
            except Exception:
                pass
        self._log_msg("Key poller worker stopped", "INFO")

    def set_enabled(self, enabled: bool):
        """Toggle polling on/off. The worker only ticks when enabled."""
        enabled = bool(enabled)
        with self._lock:
            self._state.enabled = enabled
            # Reset per-cycle bookkeeping so the UI shows a fresh "last tick".
            if enabled:
                self._state.next_tick_at = time.time() + self._interval
            else:
                self._state.next_tick_at = 0.0
        if enabled:
            self._enabled_event.set()
            self._log_msg(
                f"Auto key-pull enabled (every {self._interval:g}s)", "OK"
            )
        else:
            self._enabled_event.clear()
            self._log_msg("Auto key-pull disabled", "WARN")

    def is_enabled(self) -> bool:
        with self._lock:
            return self._state.enabled

    def snapshot(self) -> PollerSnapshot:
        with self._lock:
            return self._state.copy()

    def set_base_dir(self, base_dir: str):
        """Update base_dir at runtime (e.g. after Settings change)."""
        with self._lock:
            self._state.base_dir = base_dir
        self._base_dir = base_dir

    def run_once_now(self) -> bool:
        """Trigger an immediate tick without waiting for the interval.

        Returns True if a tick was scheduled, False if one is already running.
        """
        if self._worker is None or not self._worker.is_alive():
            self.start()
        # We can't poke the worker mid-sleep without a Condition, so the
        # simplest portable approach is: tell the worker "enable yourself"
        # and let the next interval tick fire. But for a manual "Pull now"
        # button we want it to actually run. We cheat by setting next_tick_at
        # to the past — the worker checks it on each loop iteration.
        with self._lock:
            # If already mid-tick, skip
            if self._state.running:
                return False
            self._state.next_tick_at = 0.0
        # Wake the worker by toggling the event
        was_set = self._enabled_event.is_set()
        if not was_set:
            self._enabled_event.set()
        # Revert state: if we were not enabled, restore that flag.
        # (The next loop iteration will see enabled_event set but we
        # haven't changed self._state.enabled, so this is safe.)
        # Simpler: we also flip enabled=True so the manual run is consistent.
        with self._lock:
            self._state.enabled = True
        return True

    # ------------------------------------------------------------------ internal

    def _log_msg(self, msg: str, level: str = "INFO"):
        if self._log is None:
            print(f"[key-poller] {level}: {msg}")
        else:
            try:
                self._log(msg, level)
            except Exception:
                # Never let logging failures kill the worker.
                print(f"[key-poller] {level}: {msg}")

    def _update_state(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._state, k):
                    setattr(self._state, k, v)

    def _run_pull_script(self) -> tuple[bool, str]:
        """Invoke pull_latest_key.py in a subprocess.

        Returns (success, message). On success, the message is a short
        description of what happened (key prefix or 'up to date').
        """
        script_path = os.path.join(self._base_dir, "pull_latest_key.py")
        if not os.path.exists(script_path):
            return False, f"pull_latest_key.py not found at {script_path}"

        # Use subprocess instead of importing so the script's own CLI
        # behavior (stdout banners, sys.exit codes) is preserved verbatim.
        import subprocess

        try:
            # CREATE_NO_WINDOW keeps the console hidden on Windows.
            kwargs = {
                "cwd": self._base_dir,
                "capture_output": True,
                "text": True,
                "timeout": SCRIPT_TIMEOUT_SEC,
                "encoding": "utf-8",
                "errors": "replace",
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            kwargs["env"] = env

            result = subprocess.run(
                [sys.executable, script_path], **kwargs
            )
        except subprocess.TimeoutExpired:
            return False, f"pull_latest_key.py timed out after {SCRIPT_TIMEOUT_SEC}s"
        except Exception as e:
            return False, f"failed to launch pull_latest_key.py: {e}"

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode != 0:
            tail = stderr or stdout or f"exit code {result.returncode}"
            return False, tail[-400:]  # cap error length

        # Parse stdout for the "Updated active_key.txt with new key: …" line
        # to extract a short prefix for UI display.
        prefix = ""
        for line in stdout.splitlines():
            line = line.strip()
            if "Updated active_key.txt with new key:" in line:
                # e.g. "Updated active_key.txt with new key: abcdef123456…"
                after = line.split(":", 1)[1].strip()
                prefix = after[:16]
                return True, f"new key: {prefix or '?'}"
            if "Key already up to date" in line:
                return True, "up to date"
        # Unknown but exit 0 — treat as success with the raw tail.
        return True, (stdout.splitlines()[-1] if stdout else "ok")[:80]

    def _tick(self):
        """Single polling iteration."""
        self._update_state(
            running=True,
            last_started_ts=time.time(),
            tick_count=self._state.tick_count + 1,
        )
        try:
            ok, msg = self._run_pull_script()
            with self._lock:
                self._state.last_finished_ts = time.time()
                self._state.last_success = ok
                if ok:
                    self._state.success_count += 1
                    self._state.last_error = ""
                    # Stash the key prefix only when we actually got a new one.
                    if msg.startswith("new key:"):
                        self._state.last_key_prefix = msg.split(":", 1)[1].strip()
                else:
                    self._state.error_count += 1
                    self._state.last_error = msg
            if ok:
                self._log_msg(f"Key pull OK — {msg}", "OK")
            else:
                # Throttle noisy error logs so a flapping network doesn't
                # flood the activity log.
                self._log_throttled_error(msg)
        except Exception as e:
            tb = traceback.format_exc(limit=2)
            with self._lock:
                self._state.last_finished_ts = time.time()
                self._state.last_success = False
                self._state.error_count += 1
                self._state.last_error = str(e)
            self._log_throttled_error(f"unhandled: {e}\n{tb}")
        finally:
            self._update_state(running=False)

    # Throttle identical error messages to at most once per 30s.
    _last_err_msg = ""
    _last_err_ts = 0.0
    _ERR_COOLDOWN = 30.0

    def _log_throttled_error(self, msg: str):
        now = time.time()
        if msg == self._last_err_msg and (now - self._last_err_ts) < self._ERR_COOLDOWN:
            return
        self._last_err_msg = msg
        self._last_err_ts = now
        self._log_msg(f"Key pull failed — {msg}", "ERROR")

    def _worker_loop(self):
        """Main worker loop. Sleeps in 250ms slices so stop() is responsive."""
        while not self._stop_event.is_set():
            # If polling is disabled, wait for it to be enabled.
            if not self._enabled_event.is_set():
                # Sleep in short slices so we can react to start/stop promptly.
                if self._stop_event.wait(timeout=0.25):
                    return
                continue

            # If a manual run set next_tick_at to 0 (past), tick now.
            with self._lock:
                next_at = self._state.next_tick_at
            now = time.time()
            if next_at > 0 and next_at > now:
                # Sleep until next tick, but in 250ms slices.
                remaining = min(next_at - now, 0.5)
                if self._stop_event.wait(timeout=remaining):
                    return
                continue
            if next_at > 0 and next_at <= now:
                # Scheduled tick — fire it.
                pass
            elif next_at == 0.0:
                # Either initial state, or a manual "run now" — fire it.
                pass

            # Tick.
            self._tick()

            # Schedule next tick.
            with self._lock:
                if self._state.enabled:
                    self._state.next_tick_at = time.time() + self._interval
                else:
                    self._state.next_tick_at = 0.0

            # Brief inter-tick sleep so we don't spin if interval is misconfigured.
            if self._stop_event.wait(timeout=0.1):
                return