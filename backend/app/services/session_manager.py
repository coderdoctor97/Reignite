"""
SessionManager — business-logic owner of session state.

This service manages the full session lifecycle:
- Manual session entry
- Manual session replacement
- Validation (via the SessionValidator adapter abstraction)
- Activation / deactivation
- Health representation

Key principles:
- A SESSION is NOT an API credential. Sessions support provider-side
  management operations; credentials are used for API access.
- MONITOR → DETECT → WARN USER → USER ACTION → VALIDATE → ACTIVATE
  → CONTINUE MONITORING
- NO automatic session renewal, rotation, or refresh (Phase 4.2+ may
  add provider-specific adapters, but renewal is always user-initiated
  or explicitly enabled per adapter)
- Session secrets are never stored in the database — only via SecretStore
- Session secrets are never returned in API responses — only masked values
- Session secrets are never logged or placed in event payloads
- All state changes are recorded as session events

State model:
    Lifecycle state (is the session in service?):
        active / inactive / expired / invalid
    Validation state (what did the last validation determine?):
        valid / invalid / expired / unknown / unavailable / error
    Health (derived, never stored):
        healthy / warning / critical / unknown

Lifecycle and validation state are deliberately separate fields.
Health is derived from both — it is never persisted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

from app.core.config import get_settings
from app.core.logging import get_logger, mask_secret
from app.core.secrets import get_secret_store, SecretStore
from app.storage.database import get_async_session
from app.storage.models import SessionRow, _utcnow
from app.storage.repositories import (
    SessionRepository,
    SessionEventRepository,
    ProviderRepository,
    EventRepository,
)

logger = get_logger("session_manager")


# ── Session states ───────────────────────────────────────────────

LIFECYCLE_STATES = ("active", "inactive", "expired", "invalid")
VALIDATION_STATES = ("valid", "invalid", "expired", "unknown", "unavailable", "error")
HEALTH_STATES = ("healthy", "warning", "critical", "unknown")

# Validation states that require user attention
_ATTENTION_VALIDATION_STATES = ("invalid", "expired")

# Minimum time between identical session events (duplicate suppression)
_DUPLICATE_SUPPRESSION_SECONDS = 300  # 5 minutes


class SessionLifecycleError(ValueError):
    """Raised when a lifecycle transition is refused (e.g. activating an
    expired session). Routes map this to HTTP 409 Conflict."""
    pass


# ── Validation result ────────────────────────────────────────────

@dataclass
class SessionValidationResult:
    """Result of a session validation attempt."""
    status: str  # 'valid','invalid','expired','unavailable','unknown','error'
    error: Optional[str] = None
    details: Optional[str] = None


# ── Validator abstraction ────────────────────────────────────────

class SessionValidator(Protocol):
    """Protocol for session validation adapters.

    Provider-specific adapters (added in later phases) implement this
    interface. The SessionManager calls this adapter — it does not
    contain provider-specific logic itself.
    """

    async def validate(
        self,
        session: SessionRow,
        secret_value: Optional[str] = None,
    ) -> SessionValidationResult:
        """Validate a session against a provider.

        Args:
            session: The session metadata row.
            secret_value: The actual secret value (if available).

        Returns:
            SessionValidationResult with status and optional error/details.
        """
        ...


class DefaultSessionValidator:
    """Default validation: returns 'unknown' when no provider-specific
    validation adapter exists.

    This is the honest answer: we can store a session secret, but without
    a provider-specific adapter we cannot determine whether the provider
    still accepts it.

    A session is NEVER reported as valid merely because its secret exists
    in the SecretStore. Missing secrets are reported as 'invalid' because
    that is a locally knowable fact.
    """

    def __init__(self, secret_store: Optional[SecretStore] = None) -> None:
        self._secret_store = secret_store or get_secret_store()

    async def validate(
        self,
        session: SessionRow,
        secret_value: Optional[str] = None,
    ) -> SessionValidationResult:
        if not session.secret_ref:
            return SessionValidationResult(
                status="invalid",
                error="No secret reference",
            )

        if secret_value is None and not self._secret_store.exists(session.secret_ref):
            return SessionValidationResult(
                status="invalid",
                error="Secret not found in store",
            )

        # The secret is stored, but without a provider-specific adapter we
        # cannot verify it against the upstream provider. Never pretend
        # existence means validity.
        return SessionValidationResult(status="unknown")


# ── Health derivation ────────────────────────────────────────────

def _is_overdue(iso_timestamp: Optional[str]) -> bool:
    """True if the given ISO timestamp is in the past."""
    if not iso_timestamp:
        return False
    try:
        due = datetime.fromisoformat(iso_timestamp)
        return datetime.now(timezone.utc) >= due
    except (ValueError, TypeError):
        return False


def derive_session_health(
    lifecycle_state: str,
    validation_state: str,
    next_validation_at: Optional[str] = None,
) -> str:
    """Derive a session health state (pure function — no side effects).

    Health states:
        healthy  — validated and valid
        warning  — validation unavailable/errored, or validation overdue
        critical — session invalid or expired
        unknown  — never validated or provider validation unsupported
    """
    if lifecycle_state in ("expired", "invalid"):
        return "critical"
    if validation_state in ("invalid", "expired"):
        return "critical"
    if validation_state == "valid":
        if _is_overdue(next_validation_at):
            return "warning"  # validation is overdue
        return "healthy"
    if validation_state in ("unavailable", "error"):
        return "warning"
    if validation_state in ("unknown",) and _is_overdue(next_validation_at):
        return "warning"  # a scheduled validation was missed
    return "unknown"


# ── Duplicate suppression ────────────────────────────────────────

async def _should_suppress_event(session_id: str, event_type: str) -> bool:
    """Check whether an identical event was created recently."""
    from sqlalchemy import select
    from app.storage.models import SessionEventRow

    async with get_async_session() as session:
        result = await session.execute(
            select(SessionEventRow)
            .where(SessionEventRow.session_id == session_id)
            .where(SessionEventRow.event_type == event_type)
            .order_by(SessionEventRow.created_at.desc())
            .limit(1)
        )
        latest = result.scalar_one_or_none()
        if latest is None:
            return False
        try:
            event_time = datetime.fromisoformat(latest.created_at)
            elapsed = (datetime.now(timezone.utc) - event_time).total_seconds()
            return elapsed < _DUPLICATE_SUPPRESSION_SECONDS
        except (ValueError, TypeError):
            return False


# ── SessionManager ───────────────────────────────────────────────

class SessionManager:
    """Manages the full session lifecycle.

    Thread-safe: all state mutations go through async sessions.
    """

    def __init__(
        self,
        secret_store: Optional[SecretStore] = None,
        validator: Optional[SessionValidator] = None,
    ) -> None:
        self._secret_store = secret_store or get_secret_store()
        self._validator = validator or DefaultSessionValidator()

    # ── Public API ───────────────────────────────────────────────

    async def list_sessions(self, provider_id: Optional[str] = None) -> list[dict]:
        """List all sessions, optionally filtered by provider.

        Returns safe metadata only — no raw secrets.
        """
        async with get_async_session() as session:
            if provider_id:
                rows = await SessionRepository.list_by_provider(session, provider_id)
            else:
                rows = await SessionRepository.list_all(session)
            return [self._row_to_dict(row) for row in rows]

    async def get_session(self, session_id: str) -> Optional[dict]:
        """Get a single session by ID. Returns safe metadata only."""
        async with get_async_session() as session:
            row = await SessionRepository.get_by_id(session, session_id)
            if row is None:
                return None
            return self._row_to_dict(row)

    async def get_active_session(self, provider_id: Optional[str] = None) -> Optional[dict]:
        """Get the currently active session.

        With provider_id: the active session for that provider.
        Without: the most recently activated session across all providers.
        """
        async with get_async_session() as session:
            if provider_id:
                row = await SessionRepository.get_active(session, provider_id)
            else:
                from sqlalchemy import select
                result = await session.execute(
                    select(SessionRow)
                    .where(SessionRow.lifecycle_state == "active")
                    .order_by(SessionRow.activated_at.desc())
                    .limit(1)
                )
                row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._row_to_dict(row)

    async def add_session(
        self,
        secret_value: str,
        provider_id: str,
        label: Optional[str] = None,
        source: str = "manual",
    ) -> dict:
        """Add a new session manually.

        The secret value is stored in the SecretStore. Only metadata and a
        masked representation are stored in the database. New sessions start
        'inactive' — activation is always an explicit user action.

        Args:
            secret_value: The raw session secret (cookie value, token, ...).
            provider_id: The provider this session belongs to.
            label: Optional user-facing label.
            source: How the session was obtained ('manual').

        Returns:
            Safe metadata dict for the created session.

        Raises:
            ValueError: If the secret is empty or the provider does not exist.
        """
        if not secret_value or not secret_value.strip():
            raise ValueError("Session secret cannot be empty")
        if not provider_id or not provider_id.strip():
            raise ValueError("provider_id is required")

        secret_value = secret_value.strip()
        label = (label or "").strip() or None

        async with get_async_session() as session:
            provider = await ProviderRepository.get_by_id(session, provider_id)
            if provider is None:
                raise ValueError(f"Provider not found: {provider_id}")

        # Store the secret OUTSIDE the database
        secret_ref = self._secret_store.store(secret_value)
        session_masked = mask_secret(secret_value)

        async with get_async_session() as session:
            row = await SessionRepository.create(
                session,
                provider_id=provider_id,
                session_masked=session_masked,
                secret_ref=secret_ref,
                label=label,
                source=source,
            )

            await SessionEventRepository.create(
                session,
                event_type="created",
                status="success",
                provider_id=provider_id,
                session_id=row.id,
                details_json=json.dumps({
                    "source": source,
                    "masked": session_masked,
                    "label": label,
                }),
            )
            await SessionEventRepository.create(
                session,
                event_type="imported_manually",
                status="success",
                provider_id=provider_id,
                session_id=row.id,
                details_json=json.dumps({"masked": session_masked}),
            )

            await self._emit_event(
                session,
                "session.created",
                "info",
                f"Session added for provider {provider_id} (masked: {session_masked})",
                {"session_id": row.id, "provider_id": provider_id, "masked": session_masked},
            )

            await session.commit()

        logger.info(
            "Session added: id=%s provider=%s masked=%s label=%s",
            row.id, provider_id, session_masked, label or "—",
        )

        return self._row_to_dict(row)

    async def validate_session(self, session_id: str) -> dict:
        """Validate a session through the validator abstraction.

        1. Verify the session exists
        2. Retrieve the secret from the SecretStore (adapter-only, never logged)
        3. Invoke the SessionValidator
        4. Update validation_state, last_validated, next_validation_at
        5. Sync lifecycle_state with the validation outcome
        6. Record events (with duplicate suppression for warnings)

        Returns:
            Updated session metadata dict.
        """
        async with get_async_session() as session:
            row = await SessionRepository.get_by_id(session, session_id)
            if row is None:
                raise ValueError(f"Session not found: {session_id}")
            provider_id = row.provider_id
            secret_ref = row.secret_ref
            provider = await ProviderRepository.get_by_id(session, provider_id)

        secret_value = None
        if secret_ref:
            secret_value = self._secret_store.retrieve(secret_ref)

        # Provider-aware validator selection: providers may declare a
        # session_validation adapter via capabilities_json; otherwise the
        # manager's validator (default: honest 'unknown') is used.
        import json as _json
        validator = self._validator
        if provider is not None:
            try:
                declared = (_json.loads(provider.capabilities_json or "{}") or {}).get("session_validation")
            except (ValueError, TypeError):
                declared = None
            if declared:
                from app.adapters.registry import get_session_validator
                validator = get_session_validator(provider)

        result = await validator.validate(row, secret_value=secret_value)

        # Defense-in-depth: never let a (future, provider-specific)
        # validator's error text echo the secret into the database,
        # events, or API responses.
        result.error = self._redact(secret_value, result.error)
        result.details = self._redact(secret_value, result.details)

        settings = get_settings()
        now = _utcnow()
        next_validation = (
            datetime.now(timezone.utc) + timedelta(seconds=settings.session_validation_interval)
        ).isoformat()

        async with get_async_session() as session:
            update_fields = {
                "validation_state": result.status,
                "last_validated": now,
                "next_validation_at": next_validation,
                "last_validation_error": result.error,  # None clears previous errors
            }
            if result.status == "valid":
                update_fields["last_successful_fetch"] = now

            # Sync lifecycle with the validation outcome:
            # invalid/expired validation removes the session from service;
            # a later valid result restores it to 'inactive' (re-activation
            # is always an explicit user action).
            if result.status in ("invalid", "expired"):
                update_fields["lifecycle_state"] = result.status
                update_fields["deactivated_at"] = now
            elif result.status == "valid" and row.lifecycle_state in ("invalid", "expired"):
                update_fields["lifecycle_state"] = "inactive"

            await SessionRepository.update_fields(session, session_id, **update_fields)

            await SessionEventRepository.create(
                session,
                event_type="validated",
                status="success" if result.status == "valid" else "failed",
                provider_id=provider_id,
                session_id=session_id,
                failure_reason=result.error,
                details_json=json.dumps({"validation_state": result.status}),
            )

            if result.status in ("invalid", "expired"):
                if not await _should_suppress_event(session_id, result.status):
                    await SessionEventRepository.create(
                        session,
                        event_type=result.status,
                        status="failed",
                        provider_id=provider_id,
                        session_id=session_id,
                        failure_reason=result.error,
                        details_json=json.dumps({
                            "detected_by": "validation",
                            "validation_state": result.status,
                        }),
                    )

            # App-wide structured event
            if result.status == "valid":
                await self._emit_event(
                    session, "session.validated", "info",
                    f"Session {session_id} validation succeeded",
                    {"session_id": session_id, "provider_id": provider_id},
                )
            elif result.status in ("invalid", "expired"):
                await self._emit_event(
                    session, f"session.{result.status}", "error",
                    f"Session {session_id} is {result.status}" + (f": {result.error}" if result.error else ""),
                    {"session_id": session_id, "provider_id": provider_id, "error": result.error},
                )
            elif result.status in ("unavailable", "error"):
                await self._emit_event(
                    session, "session.warning", "warn",
                    f"Session {session_id} validation {result.status}",
                    {"session_id": session_id, "provider_id": provider_id, "error": result.error},
                )

            await session.commit()

        logger.info(
            "Session validated: id=%s provider=%s status=%s",
            session_id, provider_id, result.status,
        )

        return await self.get_session(session_id)

    async def activate_session(self, session_id: str) -> dict:
        """Activate a session.

        1. Verify the session exists
        2. Refuse activation of expired/invalid sessions (they must be
           replaced first — monitor-first, user-controlled)
        3. Deactivate any currently active session for the same provider
        4. Activate the selected session
        5. Record session events

        Raises:
            ValueError: Session not found.
            SessionLifecycleError: Session lifecycle is expired/invalid.
        """
        async with get_async_session() as session:
            row = await SessionRepository.get_by_id(session, session_id)
            if row is None:
                raise ValueError(f"Session not found: {session_id}")

            if row.lifecycle_state in ("expired", "invalid"):
                raise SessionLifecycleError(
                    f"Session is {row.lifecycle_state} — replace the session "
                    f"credential and validate it before activating"
                )

            provider_id = row.provider_id

            # Deactivate any currently active session for this provider
            current_active = await SessionRepository.get_active(session, provider_id)
            if current_active and current_active.id != session_id:
                now = _utcnow()
                await SessionRepository.update_fields(
                    session,
                    current_active.id,
                    lifecycle_state="inactive",
                    deactivated_at=now,
                )
                await SessionEventRepository.create(
                    session,
                    event_type="deactivated",
                    status="success",
                    provider_id=current_active.provider_id,
                    session_id=current_active.id,
                    details_json=json.dumps({"reason": "replaced_by_new_activation"}),
                )
                logger.info("Deactivated previous session: id=%s", current_active.id)

            # Activate the selected session
            now = _utcnow()
            await SessionRepository.update_fields(
                session,
                session_id,
                lifecycle_state="active",
                activated_at=now,
                deactivated_at=None,
            )

            await SessionEventRepository.create(
                session,
                event_type="activated",
                status="success",
                provider_id=provider_id,
                session_id=session_id,
            )
            await self._emit_event(
                session, "session.activated", "info",
                f"Session {session_id} activated for provider {provider_id}",
                {"session_id": session_id, "provider_id": provider_id},
            )

            await session.commit()

        logger.info("Session activated: id=%s provider=%s", session_id, provider_id)
        return await self.get_session(session_id)

    async def deactivate_session(self, session_id: str) -> dict:
        """Deactivate a session.

        Prefer deactivation over deletion — there is no DELETE endpoint
        for sessions. Deactivating an already-inactive session is a no-op.

        Raises:
            ValueError: Session not found.
        """
        async with get_async_session() as session:
            row = await SessionRepository.get_by_id(session, session_id)
            if row is None:
                raise ValueError(f"Session not found: {session_id}")

            if row.lifecycle_state != "active":
                # Already inactive (or expired/invalid) — nothing to do
                return self._row_to_dict(row)

            now = _utcnow()
            await SessionRepository.update_fields(
                session,
                session_id,
                lifecycle_state="inactive",
                deactivated_at=now,
            )

            await SessionEventRepository.create(
                session,
                event_type="deactivated",
                status="success",
                provider_id=row.provider_id,
                session_id=session_id,
                details_json=json.dumps({"reason": "user_deactivated"}),
            )
            await self._emit_event(
                session, "session.deactivated", "info",
                f"Session {session_id} deactivated for provider {row.provider_id}",
                {"session_id": session_id, "provider_id": row.provider_id},
            )

            await session.commit()

        logger.info("Session deactivated: id=%s", session_id)
        return await self.get_session(session_id)

    async def check_all_due_sessions(self) -> list[dict]:
        """Validate all sessions whose next_validation_at is due.

        Called by the background monitor (Phase 4.2 integration).
        Validation only — never renewal.
        """
        from sqlalchemy import select, or_

        now = _utcnow()
        async with get_async_session() as session:
            result = await session.execute(
                select(SessionRow)
                .where(
                    or_(
                        SessionRow.next_validation_at <= now,
                        SessionRow.next_validation_at.is_(None),
                    )
                )
                .where(SessionRow.lifecycle_state.in_(["active", "inactive"]))
                .order_by(SessionRow.created_at.desc())
            )
            rows = list(result.scalars().all())

        results = []
        for row in rows:
            try:
                results.append(await self.validate_session(row.id))
            except Exception as e:
                logger.error("Failed to validate due session %s: %s", row.id, e)
        return results

    async def replace_session(
        self,
        secret_value: str,
        provider_id: str,
        label: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """Replace an existing session with a new one.

        The explicit user-initiated replacement workflow:
            existing session → user enters new session → store new session
            → validate → activate → previous session becomes inactive

        The previous session is deactivated, NOT deleted. An audit event
        chain (replacement_requested → replacement_completed) is recorded.

        If validation of the new session fails (invalid/expired), the new
        session is NOT activated and the previous session is left untouched.

        Args:
            secret_value: The new session secret.
            provider_id: The provider this session belongs to.
            label: Optional label for the new session.
            session_id: Optional explicit target. If omitted, the currently
                active session for the provider is replaced; if none is
                active, the most recent session for the provider.

        Returns:
            Safe metadata dict for the new (activated) session.

        Raises:
            ValueError: Empty secret, unknown provider, no session to
                replace, or the new session failed validation.
        """
        if not secret_value or not secret_value.strip():
            raise ValueError("Session secret cannot be empty")

        # Resolve the target session
        async with get_async_session() as session:
            if session_id:
                target = await SessionRepository.get_by_id(session, session_id)
                if target is None:
                    raise ValueError(f"Session not found: {session_id}")
                if target.provider_id != provider_id:
                    raise ValueError(
                        f"Session {session_id} belongs to provider "
                        f"{target.provider_id}, not {provider_id}"
                    )
            else:
                target = await SessionRepository.get_active(session, provider_id)
                if target is None:
                    target = await SessionRepository.get_by_provider(session, provider_id)
                if target is None:
                    raise ValueError(
                        f"No session exists for provider {provider_id} — "
                        f"add a session instead of replacing"
                    )

            # Record replacement_requested on the existing session
            await SessionEventRepository.create(
                session,
                event_type="replacement_requested",
                status="success",
                provider_id=provider_id,
                session_id=target.id,
            )
            await self._emit_event(
                session, "session.replacement_requested", "info",
                f"Session replacement requested for provider {provider_id} (replacing {target.id})",
                {"session_id": target.id, "provider_id": provider_id},
            )
            await session.commit()

        # Create the new session
        new_session = await self.add_session(secret_value, provider_id, label=label)

        # Validate the new session before activating it
        validated = await self.validate_session(new_session["id"])
        if validated["validation_state"] in ("invalid", "expired"):
            raise ValueError(
                f"New session failed validation ({validated['validation_state']})"
                + (f": {validated['last_validation_error']}" if validated["last_validation_error"] else "")
                + " — the previous session was left unchanged"
            )

        # Activate the new session (deactivates the previous one)
        result = await self.activate_session(new_session["id"])

        async with get_async_session() as session:
            await SessionEventRepository.create(
                session,
                event_type="replacement_completed",
                status="success",
                provider_id=provider_id,
                session_id=new_session["id"],
                details_json=json.dumps({
                    "new_session_id": new_session["id"],
                    "replaced_session_id": target.id,
                }),
            )
            await self._emit_event(
                session, "session.replacement_completed", "info",
                f"Session replacement completed for provider {provider_id}: "
                f"{target.id} → {new_session['id']}",
                {
                    "session_id": new_session["id"],
                    "provider_id": provider_id,
                    "replaced_session_id": target.id,
                },
            )
            await session.commit()

        logger.info(
            "Session replaced for provider %s: %s → %s",
            provider_id, target.id, new_session["id"],
        )
        return result

    # ── Internal ─────────────────────────────────────────────────

    @staticmethod
    def _redact(secret_value: Optional[str], text: Optional[str]) -> Optional[str]:
        """Replace any occurrence of the secret in text with a redaction marker."""
        if not text:
            return text
        if secret_value and secret_value in text:
            return text.replace(secret_value, "[REDACTED]")
        return text

    async def _emit_event(
        self,
        db_session,
        event_type: str,
        severity: str,
        message: str,
        details: Optional[dict] = None,
    ) -> None:
        """Emit an app-wide structured event (generic 'events' table).

        Never include secrets — only IDs and masked values.
        """
        details_json = json.dumps(details) if details else None
        await EventRepository.create(
            db_session,
            event_type=event_type,
            message=message,
            severity=severity,
            details_json=details_json,
        )

    def _row_to_dict(self, row: SessionRow) -> dict:
        """Convert a SessionRow to a safe API dict (no raw secrets, no refs)."""
        health = derive_session_health(
            row.lifecycle_state,
            row.validation_state,
            row.next_validation_at,
        )
        return {
            "id": row.id,
            "provider_id": row.provider_id,
            "label": row.label,
            "session_masked": row.session_masked,
            "source": row.source,
            "lifecycle_state": row.lifecycle_state,
            "validation_state": row.validation_state,
            "health": health,
            "last_validated": row.last_validated,
            "next_validation_at": row.next_validation_at,
            "last_validation_error": row.last_validation_error,
            "last_successful_fetch": row.last_successful_fetch,
            "activated_at": row.activated_at,
            "deactivated_at": row.deactivated_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


# Module-level singleton
_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """Return the singleton SessionManager instance."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager


def set_session_manager(manager: SessionManager) -> None:
    """Override the session manager (for testing)."""
    global _session_manager
    _session_manager = manager
