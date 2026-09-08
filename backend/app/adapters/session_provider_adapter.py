"""
SessionProviderAdapter — clean seam between session storage and future
provider-specific management adapters.

Purpose (Phase 4.1):
The legacy KeyBinder/rotate_now/pull_latest_key scripts embedded a session
cookie directly in source code. The new application must never do that.

This adapter is the ONLY path by which a raw session secret may leave the
SecretStore. A future provider-specific management adapter (e.g. an
OpusDashboardAdapter that lists/creates/deletes API keys) will call:

    adapter.get_active_session_secret(provider_id)

to obtain the raw secret, use it immediately for a single request, and
discard it. The secret is never exposed through the API layer, the
frontend, events, or logs.

Nothing in this phase connects to real provider dashboards, and nothing
here contains provider-specific logic (no domains, URLs, cookie names,
login flows, or browser automation).
"""

from __future__ import annotations

from typing import Optional, Protocol

from app.core.logging import get_logger
from app.core.secrets import get_secret_store, SecretStore
from app.storage.database import get_async_session
from app.storage.repositories import SessionRepository

logger = get_logger("session_provider_adapter")


class SessionProviderAdapter(Protocol):
    """Interface for provider-facing session access.

    Implementations hand a raw session secret to a provider-specific
    management adapter WITHOUT exposing it to the rest of the application.
    """

    async def get_active_session_secret(self, provider_id: str) -> Optional[str]:
        """Return the raw secret of the provider's active session.

        Returns None when no active session exists or its secret is
        unavailable. Callers must use the value immediately and never
        log, store, or return it.
        """
        ...


class DefaultSessionProviderAdapter:
    """Default implementation backed by the database + SecretStore.

    Retrieves the active session's secret on demand. This is deliberately
    narrow: it exposes only a transient secret value, never references
    into the store and never anything else about the session.
    """

    def __init__(self, secret_store: Optional[SecretStore] = None) -> None:
        self._secret_store = secret_store or get_secret_store()

    async def get_active_session_secret(self, provider_id: str) -> Optional[str]:
        """Return the raw secret of the provider's active session (if any).

        The secret is retrieved from the SecretStore and returned once.
        It is never logged and never cached by this adapter.
        """
        async with get_async_session() as session:
            row = await SessionRepository.get_active(session, provider_id)
            if row is None:
                logger.debug("No active session for provider %s", provider_id)
                return None
            secret_ref = row.secret_ref

        if not secret_ref:
            logger.warning(
                "Active session %s for provider %s has no secret reference",
                row.id, provider_id,
            )
            return None

        secret = self._secret_store.retrieve(secret_ref)
        if secret is None:
            logger.warning(
                "Secret not found in store for active session %s (provider %s)",
                row.id, provider_id,
            )
            return None

        logger.debug("Session secret retrieved for provider %s (session %s)", provider_id, row.id)
        return secret


# Module-level singleton
_adapter: Optional[DefaultSessionProviderAdapter] = None


def get_session_provider_adapter() -> DefaultSessionProviderAdapter:
    """Return the singleton SessionProviderAdapter instance."""
    global _adapter
    if _adapter is None:
        _adapter = DefaultSessionProviderAdapter()
    return _adapter


def set_session_provider_adapter(adapter: DefaultSessionProviderAdapter) -> None:
    """Override the adapter (for testing)."""
    global _adapter
    _adapter = adapter
