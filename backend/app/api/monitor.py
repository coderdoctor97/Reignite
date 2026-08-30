"""
Monitor API routes — credential health monitoring status and control.

Endpoints:
    GET  /api/monitor/status  — monitor status and statistics
    POST /api/monitor/run     — trigger a single monitoring cycle
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from app.services.credential_monitor import get_credential_monitor

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


# ── Response models ──────────────────────────────────────────────

class MonitorStatusResponse(BaseModel):
    """Monitor status and statistics."""
    enabled: bool
    running: bool
    interval_seconds: float
    last_run: Optional[str] = None
    last_success: Optional[str] = None
    last_error: Optional[str] = None
    credentials_checked: int = 0
    checks_succeeded: int = 0
    checks_failed: int = 0
    total_cycles: int = 0
    cycle_in_progress: bool = False


class MonitorRunResponse(BaseModel):
    """Response for a manual monitor run."""
    success: bool
    message: str
    cycle_in_progress: bool = False
    credentials_checked: int = 0
    checks_succeeded: int = 0
    checks_failed: int = 0
    health_changes: int = 0
    cycle_number: int = 0


# ── Routes ───────────────────────────────────────────────────────

@router.get("/status", response_model=MonitorStatusResponse)
async def monitor_status():
    """Return the current monitor status and statistics."""
    monitor = get_credential_monitor()
    status = monitor.status()
    return MonitorStatusResponse(
        enabled=status.enabled,
        running=status.running,
        interval_seconds=status.interval_seconds,
        last_run=status.last_run,
        last_success=status.last_success,
        last_error=status.last_error,
        credentials_checked=status.credentials_checked,
        checks_succeeded=status.checks_succeeded,
        checks_failed=status.checks_failed,
        total_cycles=status.total_cycles,
        cycle_in_progress=status.cycle_in_progress,
    )


@router.post("/run", response_model=MonitorRunResponse)
async def monitor_run():
    """Trigger a single monitoring cycle.

    Runs asynchronously. If a cycle is already running,
    returns a non-error response indicating so.
    """
    monitor = get_credential_monitor()
    result = await monitor.run_once()
    return MonitorRunResponse(
        success=result.get("success", False),
        message=result.get("message", ""),
        cycle_in_progress=result.get("cycle_in_progress", False),
        credentials_checked=result.get("credentials_checked", 0),
        checks_succeeded=result.get("checks_succeeded", 0),
        checks_failed=result.get("checks_failed", 0),
        health_changes=result.get("health_changes", 0),
        cycle_number=result.get("cycle_number", 0),
    )
