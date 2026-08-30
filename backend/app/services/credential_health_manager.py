"""
CredentialHealthManager — credential health monitoring foundation.

This service monitors credential health by:
- Determining when validation is due
- Invoking validation adapters
- Updating validation and health states
- Recording events with duplicate suppression
- Producing health summaries

Key principles:
- MONITOR → DETECT → WARN USER → USER ACTION
- Never automatically replaces credentials
- Never assumes all providers support validation
- Uses adapter pattern for provider-specific validation
- Suppresses duplicate warning events

Validation state machine:
    unknown → pending → valid
    unknown → pending → invalid
    unknown → pending → expired
    unknown → pending → unavailable
    valid   → pending → valid   (re-validation)
    valid   → pending → invalid (detected issue)
    invalid → pending → valid   (user fixed it)
    invalid → pending → invalid (still broken)

Health states (derived, not stored):
    healthy  — validation_status == 'valid'
    warning  — validation_status in ('unknown', 'unavailable', 'pending')
               or validation is due soon
    critical — validation_status in ('invalid', 'expired')
    unknown  — never validated, no validation possible
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, Protocol

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.secrets import get_secret_store, SecretStore
from app.storage.database import get_async_session
from app.storage.models import CredentialRow, _utcnow
from app.storage.repositories import (
    CredentialRepository,
    CredentialEventRepository,
)

logger = get_logger("credential_health")


# ── Validation result ────────────────────────────────────────────

@dataclass
class ValidationResult:
    """Result of a credential validation attempt."""
    status: str  # 'valid', 'invalid', 'expired', 'unavailable', 'unknown', 'error'
    error: Optional[str] = None
    details: Optional[str] = None


# ── Health states ────────────────────────────────────────────────

HEALTH_STATES = ("healthy", "warning", "critical", "unknown")


def derive_health_state(validation_status: str, next_validation_at: Optional[str] = None) -> str:
    """Derive a health state from validation status and scheduling.

    This is a pure function — no side effects, no database access.

    Health states:
        healthy  — credential is validated and valid
        warning  — validation unknown/unavailable/pending, or due soon
        critical — credential is invalid or expired
        unknown  — never validated, no validation possible
    """
    if validation_status == "valid":
        # Check if validation is due soon (within 10% of interval)
        if next_validation_at:
            try:
                next_dt = datetime.fromisoformat(next_validation_at)
                now = datetime.now(timezone.utc)
                if now >= next_dt:
                    return "warning"  # validation overdue
            except (ValueError, TypeError):
                pass
        return "healthy"

    if validation_status in ("invalid", "expired"):
        return "critical"

    if validation_status in ("unknown", "unavailable", "pending"):
        return "warning"

    return "unknown"


# ── Validator protocol ───────────────────────────────────────────

class CredentialValidator(Protocol):
    """Protocol for credential validation adapters.

    Implementations validate a credential against a specific provider.
    The health manager calls this adapter — it does not contain
    provider-specific logic itself.
    """

    async def validate(self, credential: CredentialRow, secret_value: Optional[str] = None) -> ValidationResult:
        """Validate a credential.

        Args:
            credential: The credential metadata row.
            secret_value: The actual secret value (if available).

        Returns:
            ValidationResult with status and optional error/details.
        """
        ...


# ── Default validator ────────────────────────────────────────────

class DefaultCredentialValidator:
    """Default validation: checks if the secret exists in the store.

    This is the fallback when no provider-specific validator is available.
    It does NOT make any external API calls.
    """

    def __init__(self, secret_store: Optional[SecretStore] = None) -> None:
        self._secret_store = secret_store or get_secret_store()

    async def validate(self, credential: CredentialRow, secret_value: Optional[str] = None) -> ValidationResult:
        """Check if the secret exists.

        If secret_value is provided (by the health manager), use that
        to determine if the secret exists. Otherwise fall back to
        checking the store directly.

        Returns 'unknown' if the secret exists (we can't verify against
        the provider), or 'invalid' if the secret is missing.
        """
        if not credential.secret_ref:
            return ValidationResult(
                status="invalid",
                error="No secret reference",
            )

        # If the caller already retrieved the secret, use that
        if secret_value is not None:
            return ValidationResult(status="unknown")

        # Fall back to checking the store directly
        if not self._secret_store.exists(credential.secret_ref):
            return ValidationResult(
                status="invalid",
                error="Secret not found in store",
            )

        # We know the secret is stored, but we can't verify it against
        # the upstream provider without a provider-specific adapter.
        return ValidationResult(status="unknown")


# ── Duplicate suppression ────────────────────────────────────────

# Minimum time between identical warning events for the same credential
_DUPLICATE_SUPPRESSION_SECONDS = 300  # 5 minutes


async def _should_suppress_event(
    credential_id: str,
    event_type: str,
) -> bool:
    """Check if a duplicate event should be suppressed.

    Returns True if an identical event was created recently
    (within the suppression window).
    """
    from sqlalchemy import select
    from app.storage.models import CredentialEventRow

    async with get_async_session() as session:
        # Find the most recent event of this type for this credential
        result = await session.execute(
            select(CredentialEventRow)
            .where(CredentialEventRow.credential_id == credential_id)
            .where(CredentialEventRow.event_type == event_type)
            .order_by(CredentialEventRow.created_at.desc())
            .limit(1)
        )
        latest = result.scalar_one_or_none()

        if latest is None:
            return False

        try:
            event_time = datetime.fromisoformat(latest.created_at)
            now = datetime.now(timezone.utc)
            elapsed = (now - event_time).total_seconds()
            return elapsed < _DUPLICATE_SUPPRESSION_SECONDS
        except (ValueError, TypeError):
            return False


# ── CredentialHealthManager ──────────────────────────────────────

class CredentialHealthManager:
    """Monitors credential health and manages validation scheduling.

    This service is the business-logic owner of credential health.
    It does NOT automatically replace credentials — it detects issues
    and records events so the user can take action.
    """

    def __init__(
        self,
        validator: Optional[CredentialValidator] = None,
        secret_store: Optional[SecretStore] = None,
    ) -> None:
        self._validator = validator or DefaultCredentialValidator()
        self._secret_store = secret_store or get_secret_store()

    # ── Public API ───────────────────────────────────────────────

    async def check_credential(self, credential_id: str) -> dict:
        """Run a health check on a single credential.

        1. Set validation_status to 'pending'
        2. Invoke the validation adapter
        3. Update validation_status with the result
        4. Calculate next_validation_at
        5. Record events (with duplicate suppression)
        6. Return the health summary

        Returns:
            Health summary dict.
        """
        async with get_async_session() as session:
            row = await CredentialRepository.get_by_id(session, credential_id)
            if row is None:
                raise ValueError(f"Credential not found: {credential_id}")

            # Mark as pending
            await CredentialRepository.update_fields(
                session, credential_id, validation_status="pending",
            )
            await session.commit()

        # Retrieve the secret for the validator
        secret_value = None
        async with get_async_session() as session:
            row = await CredentialRepository.get_by_id(session, credential_id)
            if row and row.secret_ref:
                secret_value = self._secret_store.retrieve(row.secret_ref)

        # Run validation — pass the row and secret value
        result = await self._validator.validate(row, secret_value=secret_value)

        # Update credential with result
        settings = get_settings()
        now = _utcnow()
        next_validation = self._calculate_next_validation(settings.credential_validation_interval)

        async with get_async_session() as session:
            update_fields = {
                "validation_status": result.status,
                "last_validated": now,
                "next_validation_at": next_validation,
            }
            if result.error:
                update_fields["last_validation_error"] = result.error

            await CredentialRepository.update_fields(session, credential_id, **update_fields)

            # Record events with duplicate suppression
            await self._record_validation_events(session, row, result)

            await session.commit()

        # Re-fetch for the return value
        async with get_async_session() as session:
            row = await CredentialRepository.get_by_id(session, credential_id)
            return self._build_health_summary(row)

    async def check_all_due_credentials(self) -> list[dict]:
        """Check all credentials whose next_validation_at <= now.

        This is the method a future background monitor would call.
        It only checks credentials that are due — not all credentials.

        Returns:
            List of health summary dicts for checked credentials.
        """
        now = _utcnow()
        results = []

        async with get_async_session() as session:
            # Find credentials due for validation
            from sqlalchemy import select, or_
            stmt = (
                select(CredentialRow)
                .where(
                    or_(
                        CredentialRow.next_validation_at <= now,
                        CredentialRow.next_validation_at.is_(None),
                    )
                )
                .where(CredentialRow.state.in_(["active", "inactive"]))
                .order_by(CredentialRow.created_at.desc())
            )
            result = await session.execute(stmt)
            rows = list(result.scalars().all())

        for row in rows:
            try:
                summary = await self.check_credential(row.id)
                results.append(summary)
            except Exception as e:
                logger.error("Failed to check credential %s: %s", row.id, e)

        return results

    async def get_health(self, credential_id: str) -> dict:
        """Get the health summary for a credential without running validation.

        Returns the current health state based on existing validation data.
        """
        async with get_async_session() as session:
            row = await CredentialRepository.get_by_id(session, credential_id)
            if row is None:
                raise ValueError(f"Credential not found: {credential_id}")
            return self._build_health_summary(row)

    async def get_all_health(self) -> list[dict]:
        """Get health summaries for all credentials.

        Returns current health states without running new validations.
        """
        async with get_async_session() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(CredentialRow).order_by(CredentialRow.created_at.desc())
            )
            rows = list(result.scalars().all())
            return [self._build_health_summary(row) for row in rows]

    # ── Internal ─────────────────────────────────────────────────

    def _calculate_next_validation(self, interval_seconds: float) -> str:
        """Calculate the next validation timestamp."""
        now = datetime.now(timezone.utc)
        next_time = now + timedelta(seconds=interval_seconds)
        return next_time.isoformat()

    async def _record_validation_events(
        self,
        session,
        row: CredentialRow,
        result: ValidationResult,
    ) -> None:
        """Record validation events with duplicate suppression."""
        # Always record the validation attempt
        await CredentialEventRepository.create(
            session,
            event_type="validated",
            status="success" if result.status == "valid" else "failed",
            provider_id=row.provider_id,
            credential_id=row.id,
            failure_reason=result.error,
            details_json=f'{{"validation_status":"{result.status}"}}',
        )

        # Record warning events for issues (with duplicate suppression)
        if result.status in ("invalid", "expired"):
            event_type = result.status  # 'invalid' or 'expired'
            if not await _should_suppress_event(row.id, event_type):
                await CredentialEventRepository.create(
                    session,
                    event_type=event_type,
                    status="failed",
                    provider_id=row.provider_id,
                    credential_id=row.id,
                    failure_reason=result.error,
                    details_json=f'{{"detected_by":"health_check","validation_status":"{result.status}"}}',
                )

        if result.status == "unavailable":
            if not await _should_suppress_event(row.id, "warning_triggered"):
                await CredentialEventRepository.create(
                    session,
                    event_type="warning_triggered",
                    status="failed",
                    provider_id=row.provider_id,
                    credential_id=row.id,
                    failure_reason="Validation unavailable",
                    details_json='{"reason":"validation_unavailable"}',
                )

    def _build_health_summary(self, row: CredentialRow) -> dict:
        """Build a health summary dict from a credential row."""
        health_state = derive_health_state(row.validation_status, row.next_validation_at)

        return {
            "credential_id": row.id,
            "provider_id": row.provider_id,
            "key_masked": row.key_masked,
            "state": row.state,
            "validation_status": row.validation_status,
            "health": health_state,
            "last_validated": row.last_validated,
            "next_validation_at": row.next_validation_at,
            "last_validation_error": row.last_validation_error,
        }


# Module-level singleton
_health_manager: Optional[CredentialHealthManager] = None


def get_credential_health_manager() -> CredentialHealthManager:
    """Return the singleton CredentialHealthManager instance."""
    global _health_manager
    if _health_manager is None:
        _health_manager = CredentialHealthManager()
    return _health_manager


def set_credential_health_manager(manager: CredentialHealthManager) -> None:
    """Override the health manager (for testing)."""
    global _health_manager
    _health_manager = manager
