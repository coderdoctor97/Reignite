"""
Usage API routes — token usage summary, snapshots, thresholds.

Usage data comes from the gateway data plane (per-credential counters)
and periodic snapshots. Thresholds are runtime settings.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.usage_manager import get_usage_manager

router = APIRouter(prefix="/api/usage", tags=["usage"])


class ThresholdsRequest(BaseModel):
    usage_limit: Optional[int] = None
    usage_warning_threshold: Optional[int] = None


@router.get("/summary")
async def usage_summary():
    """Aggregate usage across all credentials + thresholds + legacy file."""
    manager = get_usage_manager()
    summary = await manager.get_summary()
    summary["legacy"] = manager.read_legacy_usage()
    return summary


@router.get("/snapshots")
async def usage_snapshots(limit: int = 100):
    manager = get_usage_manager()
    snapshots = await manager.list_snapshots(limit=min(max(limit, 1), 1000))
    return {"snapshots": snapshots, "total": len(snapshots)}


@router.post("/capture")
async def capture_usage():
    """Capture a snapshot of the current counters now."""
    manager = get_usage_manager()
    return await manager.capture_snapshot()


@router.get("/thresholds")
async def get_thresholds():
    manager = get_usage_manager()
    return await manager.get_thresholds()


@router.put("/thresholds")
async def set_thresholds(request: ThresholdsRequest):
    manager = get_usage_manager()
    try:
        return await manager.set_thresholds(
            usage_limit=request.usage_limit,
            usage_warning_threshold=request.usage_warning_threshold,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
