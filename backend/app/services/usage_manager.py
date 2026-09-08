"""
UsageManager — token usage tracking, thresholds, and snapshots.

Sources of truth:
- Per-credential counters (credentials.usage_*), updated by the gateway
  data plane on every completion.
- usage_snapshots rows, captured periodically (monitor) or on demand.

Thresholds (runtime settings, stored in the settings table with env
defaults): usage_limit and usage_warning_threshold. Crossing the warning
threshold emits a `usage.warning` event (duplicate-suppressed).

Optional legacy compatibility: read token_usage.json from the legacy
directory if present (the legacy gateway's file format), so old usage
history is visible without the legacy scripts running.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.storage.database import get_async_session
from app.storage.models import CredentialRow, UsageSnapshotRow, _utcnow
from app.storage.repositories import UsageRepository, EventRepository, SettingsRepository

logger = get_logger("usage_manager")

SETTING_LIMIT = "usage_limit"
SETTING_WARNING = "usage_warning_threshold"


class UsageManager:
    """Usage business logic."""

    # ── Thresholds (runtime settings with env fallback) ──────────

    async def get_thresholds(self) -> dict:
        settings = get_settings()
        async with get_async_session() as session:
            limit_str = await SettingsRepository.get(session, SETTING_LIMIT)
            warning_str = await SettingsRepository.get(session, SETTING_WARNING)
        limit = int(limit_str) if limit_str else settings.usage_limit
        warning = int(warning_str) if warning_str else settings.usage_warning_threshold
        return {"usage_limit": limit, "usage_warning_threshold": warning}

    async def set_thresholds(self, usage_limit: Optional[int], usage_warning_threshold: Optional[int]) -> dict:
        if usage_limit is not None and usage_limit <= 0:
            raise ValueError("usage_limit must be positive")
        if usage_warning_threshold is not None and usage_warning_threshold <= 0:
            raise ValueError("usage_warning_threshold must be positive")
        async with get_async_session() as session:
            if usage_limit is not None:
                await SettingsRepository.set(session, SETTING_LIMIT, str(usage_limit))
            if usage_warning_threshold is not None:
                await SettingsRepository.set(session, SETTING_WARNING, str(usage_warning_threshold))
            await session.commit()
        return await self.get_thresholds()

    # ── Summary ──────────────────────────────────────────────────

    async def get_summary(self) -> dict:
        """Aggregate usage across all credentials plus the latest snapshot."""
        async with get_async_session() as session:
            result = await session.execute(select(CredentialRow))
            rows = list(result.scalars().all())
            latest = await UsageRepository.get_latest(session)

        total_input = sum(r.usage_input or 0 for r in rows)
        total_output = sum(r.usage_output or 0 for r in rows)
        total = sum(r.usage_total or 0 for r in rows)
        thresholds = await self.get_thresholds()
        limit = thresholds["usage_limit"]
        remaining = max(limit - total, 0)
        percent = round(total / limit * 100, 2) if limit else 0.0
        warning = total >= thresholds["usage_warning_threshold"]

        return {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total,
            "remaining": remaining,
            "limit": limit,
            "percent_used": percent,
            "warning_threshold": thresholds["usage_warning_threshold"],
            "warning_triggered": warning,
            "last_snapshot": latest.snapshot_at if latest else None,
            "credentials": [
                {
                    "credential_id": r.id,
                    "provider_id": r.provider_id,
                    "key_masked": r.key_masked,
                    "state": r.state,
                    "input_tokens": r.usage_input or 0,
                    "output_tokens": r.usage_output or 0,
                    "total_tokens": r.usage_total or 0,
                }
                for r in rows
            ],
        }

    # ── Snapshots ────────────────────────────────────────────────

    async def capture_snapshot(self) -> dict:
        """Snapshot current credential counters into usage_snapshots.

        Also imports legacy token_usage.json when present, and emits a
        duplicate-suppressed usage.warning event when the threshold is
        crossed.
        """
        async with get_async_session() as session:
            result = await session.execute(select(CredentialRow))
            rows = list(result.scalars().all())
        total_input = sum(r.usage_input or 0 for r in rows)
        total_output = sum(r.usage_output or 0 for r in rows)
        total = sum(r.usage_total if (r.usage_total or 0) else (r.usage_input or 0) + (r.usage_output or 0) for r in rows)
        if not total and (total_input or total_output):
            total = total_input + total_output

        active = next((r for r in rows if r.state == "active"), None)
        thresholds = await self.get_thresholds()

        snapshot = await UsageRepository.create(
            session,
            input_tokens=total_input,
            output_tokens=total_output,
            total_tokens=total,
            remaining=max(thresholds["usage_limit"] - total, 0),
            limit=thresholds["usage_limit"],
            credential_id=active.id if active else None,
            provider_id=active.provider_id if active else None,
        )

        # Threshold warning event (duplicate-suppressed)
        if total >= thresholds["usage_warning_threshold"]:
            from app.storage.models import EventRow
            recent = await session.execute(
                select(EventRow)
                .where(EventRow.event_type == "usage.warning")
                .order_by(EventRow.created_at.desc())
                .limit(1)
            )
            latest = recent.scalar_one_or_none()
            suppress = False
            if latest is not None:
                try:
                    event_time = datetime.fromisoformat(latest.created_at)
                    elapsed = (datetime.now(timezone.utc) - event_time).total_seconds()
                    suppress = elapsed < 300  # 5 minutes
                except (ValueError, TypeError):
                    pass
            if not suppress:
                await EventRepository.create(
                    session,
                    event_type="usage.warning",
                    severity="warn",
                    message=(
                        f"Token usage {total:,} reached the warning threshold "
                        f"({thresholds['usage_warning_threshold']:,}). "
                        f"Remaining: {max(thresholds['usage_limit'] - total, 0):,}"
                    ),
                    details_json=json.dumps({
                        "total_tokens": total,
                        "limit": thresholds["usage_limit"],
                        "threshold": thresholds["usage_warning_threshold"],
                    }),
                )

        await session.commit()
        logger.info("Usage snapshot captured: total=%d", total)
        return {
            "snapshot_id": snapshot.id,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total,
            "remaining": max(thresholds["usage_limit"] - total, 0),
            "limit": thresholds["usage_limit"],
            "snapshot_at": snapshot.snapshot_at,
        }

    async def list_snapshots(self, limit: int = 100) -> list[dict]:
        async with get_async_session() as session:
            rows = await UsageRepository.list_recent(session, limit=limit)
            return [
                {
                    "id": r.id,
                    "credential_id": r.credential_id,
                    "provider_id": r.provider_id,
                    "input_tokens": r.input_tokens,
                    "output_tokens": r.output_tokens,
                    "total_tokens": r.total_tokens,
                    "remaining": r.remaining,
                    "limit": r.limit,
                    "snapshot_at": r.snapshot_at,
                }
                for r in rows
            ]

    # ── Legacy import ────────────────────────────────────────────

    def read_legacy_usage(self) -> Optional[dict]:
        """Best-effort read of the legacy gateway's token_usage.json."""
        try:
            path = Path(get_settings().legacy_base_dir) / "token_usage.json"
            if not path.exists():
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            return {
                "input": data.get("input", 0),
                "output": data.get("output", 0),
                "total": data.get("total", 0),
                "remaining": data.get("remaining"),
                "last_updated": data.get("last_updated"),
                "key_id": data.get("key_id"),
            }
        except Exception as e:
            logger.warning("Failed to read legacy usage file: %s", e)
            return None


# Module-level singleton
_manager: Optional[UsageManager] = None


def get_usage_manager() -> UsageManager:
    global _manager
    if _manager is None:
        _manager = UsageManager()
    return _manager


def set_usage_manager(manager: UsageManager) -> None:
    global _manager
    _manager = manager
