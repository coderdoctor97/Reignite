"""
Gateway control API — the control plane's view of the first-party
gateway data plane (served in-process under /v1).

Endpoints:
    GET  /api/gateway/status   — state, stats, endpoint contract
    GET  /api/gateway/health   — liveness of the data plane
    GET  /api/gateway/config   — configuration + stable endpoint URL
    POST /api/gateway/start    — enable the data plane
    POST /api/gateway/stop     — disable the data plane (503 on /v1)
    POST /api/gateway/restart  — stop + start

The data plane runs inside the backend process; start/stop is a runtime
toggle, not a subprocess. Clients connect to the stable endpoint
{gateway_public_base_url}/v1.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings
from app.services.gateway_service import get_gateway_service

router = APIRouter(prefix="/api/gateway", tags=["gateway"])


class GatewayStatusResponse(BaseModel):
    state: str
    enabled: bool
    started_at: Optional[str] = None
    total_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_errors: int = 0
    error_rate: float = 0.0
    last_request_at: Optional[str] = None
    last_error: Optional[str] = None
    requests_by_model: dict = {}
    endpoint_url: str
    base_path: str


class GatewayActionResponse(BaseModel):
    success: bool
    message: str
    status: GatewayStatusResponse


class GatewayHealthResponse(BaseModel):
    healthy: bool
    state: str
    base_path: str
    detail: Optional[str] = None


class GatewayConfigResponse(BaseModel):
    enabled: bool
    base_path: str
    endpoint_url: str
    public_base_url: str
    auth_token_configured: bool
    upstream_timeout: float
    default_max_tokens: int


def _status() -> GatewayStatusResponse:
    service = get_gateway_service()
    status = service.status()
    settings = get_settings()
    base_url = settings.gateway_public_base_url.rstrip("/")
    return GatewayStatusResponse(
        state=status["state"],
        enabled=status["enabled"],
        started_at=status["started_at"],
        total_requests=status["total_requests"],
        total_input_tokens=status["total_input_tokens"],
        total_output_tokens=status["total_output_tokens"],
        total_errors=status["total_errors"],
        error_rate=status["error_rate"],
        last_request_at=status["last_request_at"],
        last_error=status["last_error"],
        requests_by_model=status["requests_by_model"],
        endpoint_url=f"{base_url}{settings.gateway_base_path}",
        base_path=settings.gateway_base_path,
    )


@router.get("/status", response_model=GatewayStatusResponse)
async def gateway_status():
    """Return the gateway data plane state and statistics."""
    return _status()


@router.get("/health", response_model=GatewayHealthResponse)
async def gateway_health():
    """Return gateway health (in-process — always alive when enabled)."""
    service = get_gateway_service()
    settings = get_settings()
    enabled = service.enabled()
    return GatewayHealthResponse(
        healthy=enabled,
        state="running" if enabled else "stopped",
        base_path=settings.gateway_base_path,
        detail=None if enabled else "Gateway data plane is stopped",
    )


@router.get("/config", response_model=GatewayConfigResponse)
async def gateway_config():
    """Return the gateway configuration and stable endpoint contract."""
    service = get_gateway_service()
    settings = get_settings()
    base_url = settings.gateway_public_base_url.rstrip("/")
    return GatewayConfigResponse(
        enabled=service.enabled(),
        base_path=settings.gateway_base_path,
        endpoint_url=f"{base_url}{settings.gateway_base_path}",
        public_base_url=base_url,
        auth_token_configured=bool(settings.gateway_api_key),
        upstream_timeout=settings.gateway_upstream_timeout,
        default_max_tokens=settings.gateway_default_max_tokens,
    )


@router.post("/start", response_model=GatewayActionResponse)
async def gateway_start():
    """Enable the gateway data plane. Idempotent."""
    service = get_gateway_service()
    await service.start()
    return GatewayActionResponse(success=True, message="Gateway enabled", status=_status())


@router.post("/stop", response_model=GatewayActionResponse)
async def gateway_stop():
    """Disable the gateway data plane — /v1 returns 503 until re-enabled."""
    service = get_gateway_service()
    await service.stop()
    return GatewayActionResponse(success=True, message="Gateway stopped", status=_status())


@router.post("/restart", response_model=GatewayActionResponse)
async def gateway_restart():
    """Restart the gateway data plane (disable + enable)."""
    service = get_gateway_service()
    await service.restart()
    return GatewayActionResponse(success=True, message="Gateway restarted", status=_status())
