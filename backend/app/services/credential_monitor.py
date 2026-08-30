"""
CredentialMonitor — background monitoring service for credential health.

This service periodically checks credentials that are due for validation
by delegating to CredentialHealthManager. It is an orchestration layer —
it does not contain validation or health logic itself.

Architecture:
    CredentialMonitor (this file)
           ↓
    CredentialHealthManager
           ↓
    CredentialValidator
           ↓
    CredentialRepository

Key design decisions:
- asyncio-based background task (no uncontrolled threads)
- Non-overlapping cycles (if one cycle is running, skip the next)
- Individual credential failures don't stop the monitor
- Emits structured events for health changes
- Clean shutdown via asyncio task cancellation
- Configurable interval (default: 60 seconds)
- Does NOT automatically replace credentials
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.credential_health_manager import (
    get_credential_health_manager,
    CredentialHealthManager,
    derive_health_state,
)

logger = get_logger("credential_monitor")


# ── Monitor status ───────────────────────────────────────────────

@dataclass
class MonitorStatus:
    """Snapshot of the monitor's current state."""
    enabled: bool = False
    running: bool = False
    interval_seconds: float = 60.0
    last_run: Optional[str] = None
    last_success: Optional[str] = None
    last_error: Optional[str] = None
    credentials_checked: int = 0
    checks_succeeded: int = 0
    checks_failed: int = 0
    total_cycles: int = 0
    cycle_in_progress: bool = False


# ── CredentialMonitor ────────────────────────────────────────────

class CredentialMonitor:
    """Background monitoring service for credential health.

    Periodically calls CredentialHealthManager.check_all_due_credentials()
    to validate credentials that are due. Emits events for health changes.
    """

    def __init__(
        self,
        health_manager: Optional[CredentialHealthManager] = None,
    ) -> None:
        self._health_manager = health_manager or get_credential_health_manager()
        self._task: Optional[asyncio.Task] = None
        self._enabled: bool = False
        self._running: bool = False
        self._cycle_lock = asyncio.Lock()

        # Statistics
        self._last_run: Optional[str] = None
        self._last_success: Optional[str] = None
        self._last_error: Optional[str] = None
        self._credentials_checked: int = 0
        self._checks_succeeded: int = 0
        self._checks_failed: int = 0
        self._total_cycles: int = 0

        # Health state tracking for change detection
        self._previous_health_states: dict[str, str] = {}

    # ── Public API ───────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background monitoring task.

        Idempotent: if already running, does nothing.
        """
        if self._task is not None and not self._task.done():
            logger.info("Monitor already running")
            return

        settings = get_settings()
        if not settings.credential_monitor_enabled:
            logger.info("Credential monitor is disabled in configuration")
            return

        self._enabled = True
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info(
            "Credential monitor started (interval: %.0fs)",
            settings.credential_monitor_interval,
        )

    async def stop(self) -> None:
        """Stop the background monitoring task.

        Waits for any in-progress cycle to complete.
        """
        self._enabled = False
        self._running = False

        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("Credential monitor stopped")

    async def run_once(self) -> dict:
        """Trigger a single monitoring cycle.

        If a cycle is already running, returns a status indicating so.
        Does not start a second overlapping cycle.

        Returns:
            Cycle result dict.
        """
        if self._cycle_lock.locked():
            return {
                "success": False,
                "message": "Monitor cycle already in progress",
                "cycle_in_progress": True,
            }

        async with self._cycle_lock:
            return await self._run_cycle()

    def status(self) -> MonitorStatus:
        """Return the current monitor status."""
        settings = get_settings()
        return MonitorStatus(
            enabled=self._enabled and settings.credential_monitor_enabled,
            running=self._running and self._task is not None and not self._task.done(),
            interval_seconds=settings.credential_monitor_interval,
            last_run=self._last_run,
            last_success=self._last_success,
            last_error=self._last_error,
            credentials_checked=self._credentials_checked,
            checks_succeeded=self._checks_succeeded,
            checks_failed=self._checks_failed,
            total_cycles=self._total_cycles,
            cycle_in_progress=self._cycle_lock.locked(),
        )

    def is_running(self) -> bool:
        """Check if the monitor task is running."""
        return self._running and self._task is not None and not self._task.done()

    # ── Internal ─────────────────────────────────────────────────

    async def _monitor_loop(self) -> None:
        """Main monitoring loop. Runs until stopped."""
        settings = get_settings()
        interval = settings.credential_monitor_interval

        logger.info("Monitor loop started (interval: %.0fs)", interval)

        while self._enabled:
            try:
                # Wait for the interval (or until stopped)
                await asyncio.sleep(interval)

                if not self._enabled:
                    break

                # Skip if a cycle is already running (non-overlapping)
                if self._cycle_lock.locked():
                    logger.debug("Skipping cycle — previous cycle still running")
                    continue

                async with self._cycle_lock:
                    await self._run_cycle()

            except asyncio.CancelledError:
                logger.info("Monitor loop cancelled")
                break
            except Exception as e:
                logger.error("Monitor loop error: %s", e)
                self._last_error = str(e)
                # Continue running — don't let one error stop the monitor
                await asyncio.sleep(5)  # Brief pause before retrying

        logger.info("Monitor loop exited")

    async def _run_cycle(self) -> dict:
        """Run a single monitoring cycle.

        Must be called with _cycle_lock held (or via run_once which acquires it).
        """
        now = datetime.now(timezone.utc).isoformat()
        self._last_run = now
        self._total_cycles += 1

        logger.info("Monitor cycle %d starting", self._total_cycles)

        try:
            # Check all due credentials
            results = await self._health_manager.check_all_due_credentials()

            # Process results and detect health changes
            cycle_succeeded = 0
            cycle_failed = 0
            health_changes = []

            for result in results:
                cred_id = result.get("credential_id", "unknown")
                health = result.get("health", "unknown")
                validation_status = result.get("validation_status", "unknown")

                # Track success/failure
                if validation_status in ("valid", "unknown"):
                    cycle_succeeded += 1
                else:
                    cycle_failed += 1

                # Detect health state changes
                previous = self._previous_health_states.get(cred_id)
                if previous is not None and previous != health:
                    health_changes.append({
                        "credential_id": cred_id,
                        "provider_id": result.get("provider_id"),
                        "key_masked": result.get("key_masked"),
                        "previous_health": previous,
                        "new_health": health,
                        "validation_status": validation_status,
                    })

                # Update tracked state
                self._previous_health_states[cred_id] = health

            # Update statistics
            self._credentials_checked += len(results)
            self._checks_succeeded += cycle_succeeded
            self._checks_failed += cycle_failed
            self._last_success = now

            # Emit events for health changes
            for change in health_changes:
                await self._emit_health_change_event(change)

            # Emit cycle completed event
            await self._emit_event(
                "monitor.cycle_completed",
                "info",
                f"Monitor cycle {self._total_cycles} completed: "
                f"{len(results)} credentials checked, "
                f"{cycle_succeeded} succeeded, {cycle_failed} failed, "
                f"{len(health_changes)} health changes",
            )

            logger.info(
                "Monitor cycle %d completed: %d checked, %d succeeded, %d failed, %d health changes",
                self._total_cycles, len(results), cycle_succeeded, cycle_failed, len(health_changes),
            )

            return {
                "success": True,
                "message": f"Cycle completed: {len(results)} credentials checked",
                "credentials_checked": len(results),
                "checks_succeeded": cycle_succeeded,
                "checks_failed": cycle_failed,
                "health_changes": len(health_changes),
                "cycle_number": self._total_cycles,
            }

        except Exception as e:
            self._last_error = str(e)
            logger.error("Monitor cycle %d failed: %s", self._total_cycles, e)

            await self._emit_event(
                "monitor.error",
                "error",
                f"Monitor cycle {self._total_cycles} failed: {e}",
            )

            return {
                "success": False,
                "message": f"Cycle failed: {e}",
                "cycle_number": self._total_cycles,
            }

    async def _emit_health_change_event(self, change: dict) -> None:
        """Emit an event for a credential health state change."""
        new_health = change["new_health"]
        previous_health = change["previous_health"]
        cred_id = change["credential_id"]
        provider_id = change.get("provider_id", "unknown")
        key_masked = change.get("key_masked", "—")

        # Determine event type based on new health state
        if new_health == "critical":
            event_type = "credential.critical"
            severity = "error"
        elif new_health == "warning":
            event_type = "credential.warning"
            severity = "warn"
        elif new_health == "healthy" and previous_health in ("critical", "warning"):
            event_type = "credential.health_changed"
            severity = "info"
        else:
            event_type = "credential.health_changed"
            severity = "info"

        message = (
            f"Credential {key_masked} health changed: "
            f"{previous_health} → {new_health} "
            f"(validation: {change.get('validation_status', 'unknown')})"
        )

        await self._emit_event(event_type, severity, message, details={
            "credential_id": cred_id,
            "provider_id": provider_id,
            "key_masked": key_masked,
            "previous_health": previous_health,
            "new_health": new_health,
            "validation_status": change.get("validation_status"),
        })

    async def _emit_event(
        self,
        event_type: str,
        severity: str,
        message: str,
        details: Optional[dict] = None,
    ) -> None:
        """Emit an application event."""
        try:
            from app.storage.database import get_async_session
            from app.storage.repositories import EventRepository
            import json

            details_json = json.dumps(details) if details else None

            async with get_async_session() as session:
                await EventRepository.create(
                    session,
                    event_type=event_type,
                    message=message,
                    severity=severity,
                    details_json=details_json,
                )
                await session.commit()
        except Exception as e:
            logger.warning("Failed to emit event %s: %s", event_type, e)


# Module-level singleton
_monitor: Optional[CredentialMonitor] = None


def get_credential_monitor() -> CredentialMonitor:
    """Return the singleton CredentialMonitor instance."""
    global _monitor
    if _monitor is None:
        _monitor = CredentialMonitor()
    return _monitor


def set_credential_monitor(monitor: CredentialMonitor) -> None:
    """Override the monitor (for testing)."""
    global _monitor
    _monitor = monitor
