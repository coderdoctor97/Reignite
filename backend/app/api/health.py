"""
Health check endpoint for the Gateway Control Center.

GET /api/health — returns a simple status response.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Response model for the health endpoint."""
    status: str
    version: str
    app: str


@router.get("/api/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Return application health status.

    This is the minimal endpoint that proves the backend is running.
    Future phases will add deeper health checks (database, gateway, providers).
    """
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        app=settings.app_name,
    )
