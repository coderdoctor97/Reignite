"""
Gateway API routes — lifecycle control, health monitoring, and configuration.

Endpoints:
    GET  /api/gateway/status   — process state snapshot with endpoint info
    GET  /api/gateway/health   — health check (process + port + HTTP probe)
    GET  /api/gateway/config   — gateway configuration and endpoint contract
    POST /api/gateway/start    — start the gateway
    POST /api/gateway/stop     — stop the gateway
    POST /api/gateway/restart  — restart the gateway
    GET  /api/gateway/logs     — recent subprocess output
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from app.services.gateway_manager import get_gateway_manager, GatewayState, HealthStatus

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


# ── Response models ──────────────────────────────────────────────

class EndpointInfo(BaseModel):
    """Stable endpoint contract — the local URL clients connect to."""
    host: str
    port: int
    protocol: str
    base_path: str
    url: str
    base_url: str


class ProcessInfo(BaseModel):
    """Gateway process state."""
    state: str
    pid: Optional[int] = None
    uptime_seconds: Optional[float] = None
    restart_count: int = 0
    last_exit_code: Optional[int] = None
    last_error: Optional[str] = None
    start_time: Optional[str] = None
    stop_time: Optional[str] = None
    command: Optional[str] = None
    working_dir: Optional[str] = None


class GatewayStatusResponse(BaseModel):
    """Full gateway status: process + endpoint."""
    process: ProcessInfo
    endpoint: EndpointInfo


class GatewayHealthResponse(BaseModel):
    """Gateway health check result."""
    status: str
    process_alive: bool
    port_reachable: bool
    http_responsive: bool
    checked_at: str


class GatewayActionResponse(BaseModel):
    """Response for start/stop/restart actions."""
    success: bool
    message: str
    status: GatewayStatusResponse


class GatewayLogLine(BaseModel):
    """A single line of subprocess output."""
    stream: str
    text: str
    timestamp: str


class GatewayLogsResponse(BaseModel):
    """Recent subprocess output."""
    lines: list[GatewayLogLine]
    total: int


class GatewayConfigResponse(BaseModel):
    """Gateway configuration and endpoint contract."""
    host: str
    port: int
    protocol: str
    base_path: str
    endpoint_url: str
    base_url: str
    script: str
    working_directory: str
    startup_timeout: float
    shutdown_timeout: float


# ── Helpers ──────────────────────────────────────────────────────

def _build_endpoint_info() -> EndpointInfo:
    """Build endpoint info from settings."""
    from app.core.config import get_settings
    settings = get_settings()
    return EndpointInfo(
        host=settings.gateway_host,
        port=settings.gateway_port,
        protocol=settings.gateway_protocol,
        base_path=settings.gateway_base_path,
        url=settings.gateway_endpoint_url,
        base_url=settings.gateway_base_url,
    )


def _status_from_info(info) -> GatewayStatusResponse:
    """Build a full status response from ProcessInfo."""
    return GatewayStatusResponse(
        process=ProcessInfo(
            state=info.state.value,
            pid=info.pid,
            uptime_seconds=info.uptime_seconds,
            restart_count=info.restart_count,
            last_exit_code=info.last_exit_code,
            last_error=info.last_error,
            start_time=info.start_time,
            stop_time=info.stop_time,
            command=info.command,
            working_dir=info.working_dir,
        ),
        endpoint=_build_endpoint_info(),
    )


# ── Routes ───────────────────────────────────────────────────────

@router.get("/status", response_model=GatewayStatusResponse)
async def gateway_status():
    """Return the current gateway process state and endpoint info."""
    manager = get_gateway_manager()
    info = await manager.status()
    return _status_from_info(info)


@router.get("/health", response_model=GatewayHealthResponse)
async def gateway_health():
    """Return gateway health (process alive + port reachable + HTTP probe)."""
    manager = get_gateway_manager()
    result = await manager.health()
    return GatewayHealthResponse(**result)


@router.get("/config", response_model=GatewayConfigResponse)
async def gateway_config():
    """Return the gateway configuration and stable endpoint contract.

    This is the control-plane view of the gateway configuration.
    The endpoint URL is the stable contract that clients should use.
    """
    manager = get_gateway_manager()
    config = manager.get_config()
    return GatewayConfigResponse(**config)


@router.post("/start", response_model=GatewayActionResponse)
async def gateway_start():
    """Start the gateway process. Idempotent — safe to call if already running."""
    manager = get_gateway_manager()
    info = await manager.start()
    success = info.state == GatewayState.RUNNING
    message = "Gateway started" if success else (info.last_error or "Gateway failed to start")
    return GatewayActionResponse(
        success=success,
        message=message,
        status=_status_from_info(info),
    )


@router.post("/stop", response_model=GatewayActionResponse)
async def gateway_stop():
    """Stop the gateway process. Idempotent — safe to call if already stopped."""
    manager = get_gateway_manager()
    info = await manager.stop()
    success = info.state == GatewayState.STOPPED
    message = "Gateway stopped" if success else (info.last_error or "Failed to stop gateway")
    return GatewayActionResponse(
        success=success,
        message=message,
        status=_status_from_info(info),
    )


@router.post("/restart", response_model=GatewayActionResponse)
async def gateway_restart():
    """Restart the gateway process (stop + start)."""
    manager = get_gateway_manager()
    info = await manager.restart()
    success = info.state == GatewayState.RUNNING
    message = "Gateway restarted" if success else (info.last_error or "Gateway failed to restart")
    return GatewayActionResponse(
        success=success,
        message=message,
        status=_status_from_info(info),
    )


@router.get("/logs", response_model=GatewayLogsResponse)
async def gateway_logs(limit: int = 100):
    """Return recent gateway subprocess output."""
    manager = get_gateway_manager()
    lines = manager.get_output(limit=limit)
    return GatewayLogsResponse(
        lines=[GatewayLogLine(**line) for line in lines],
        total=len(lines),
    )
