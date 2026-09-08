"""
Event API routes — structured application event log viewer.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from sqlalchemy import select

from app.storage.database import get_async_session
from app.storage.models import EventRow
from app.storage.repositories import EventRepository

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
async def list_events(
    limit: int = 100,
    severity: Optional[str] = None,
    event_type: Optional[str] = None,
):
    """List recent events, optionally filtered by severity and type."""
    limit = min(max(limit, 1), 1000)
    async with get_async_session() as session:
        stmt = select(EventRow).order_by(EventRow.created_at.desc()).limit(limit)
        if severity:
            stmt = stmt.where(EventRow.severity == severity)
        if event_type:
            stmt = stmt.where(EventRow.event_type.like(f"{event_type}%"))
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        return {
            "events": [
                {
                    "id": r.id,
                    "event_type": r.event_type,
                    "severity": r.severity,
                    "message": r.message,
                    "details_json": r.details_json,
                    "created_at": r.created_at,
                }
                for r in rows
            ],
            "total": len(rows),
        }


@router.get("/types")
async def event_types():
    """Distinct event type prefixes for filtering."""
    async with get_async_session() as session:
        result = await session.execute(
            select(EventRow.event_type).distinct().order_by(EventRow.event_type)
        )
        types = [r[0] for r in result.all()]
        prefixes = sorted({t.split(".")[0] for t in types})
        return {"types": types, "prefixes": prefixes}
