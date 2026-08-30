"""
CredentialManager — business-logic owner of credential state.

This service manages the full credential lifecycle:
- Manual credential entry (import)
- Validation (via provider-specific adapters)
- Activation / deactivation
- Replacement (explicit user-initiated workflow)
- Health monitoring

Key principles:
- MONITOR → DETECT → WARN → USER ACTION → VALIDATE → ACTIVATE → CONTINUE MONITORING
- No autonomous credential rotation
- No automatic credential generation or deletion
- Secrets are never stored in the database — only via SecretStore
- Secrets are never returned in API responses — only masked representations
- All state changes are recorded as credential events

The legacy gateway reads active_key.txt every 3 seconds. When we activate
a credential, the LegacyCredentialAdapter writes it there. The gateway
discovers the change on its own — no restart needed.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from app.core.logging import get_logger, mask_secret
from app.core.secrets import get_secret_store, SecretStore
from app.storage.database import get_async_session
from app.storage.models import CredentialRow, _utcnow
from app.storage.repositories import (
    CredentialRepository,
    CredentialEventRepository,
    ProviderRepository,
)
from app.adapters.legacy_credential_store import (
    get_legacy_credential_adapter,
    LegacyCredentialAdapter,
)

logger = get_logger("credential_manager")


# ── Credential states ────────────────────────────────────────────

CREDENTIAL_STATES = ("active", "inactive", "expired", "invalid", "revoked")
VALIDATION_STATUSES = ("valid", "invalid", "expired", "unknown")


# ── CredentialManager ────────────────────────────────────────────

class CredentialManager:
    """Manages the full credential lifecycle.

    Thread-safe: all state mutations go through async sessions.
    """

    def __init__(
        self,
        secret_store: Optional[SecretStore] = None,
        legacy_adapter: Optional[LegacyCredentialAdapter] = None,
    ) -> None:
        self._secret_store = secret_store or get_secret_store()
        self._legacy_adapter = legacy_adapter or get_legacy_credential_adapter()

    # ── Public API ───────────────────────────────────────────────

    async def list_credentials(self, provider_id: Optional[str] = None) -> list[dict]:
        """List all credentials, optionally filtered by provider.

        Returns safe metadata only — no raw secrets.
        """
        async with get_async_session() as session:
            if provider_id:
                rows = await CredentialRepository.list_by_provider(session, provider_id)
            else:
                # List all credentials across all providers
                from sqlalchemy import select
                result = await session.execute(
                    select(CredentialRow).order_by(CredentialRow.created_at.desc())
                )
                rows = list(result.scalars().all())
            return [self._row_to_dict(row) for row in rows]

    async def get_credential(self, credential_id: str) -> Optional[dict]:
        """Get a single credential by ID. Returns safe metadata only."""
        async with get_async_session() as session:
            row = await CredentialRepository.get_by_id(session, credential_id)
            if row is None:
                return None
            return self._row_to_dict(row)

    async def get_active_credential(self, provider_id: Optional[str] = None) -> Optional[dict]:
        """Get the currently active credential for a provider.

        If no provider_id is given, returns the most recently activated
        credential across all providers.
        """
        async with get_async_session() as session:
            if provider_id:
                row = await CredentialRepository.get_active(session, provider_id)
            else:
                from sqlalchemy import select
                result = await session.execute(
                    select(CredentialRow)
                    .where(CredentialRow.state == "active")
                    .order_by(CredentialRow.activated_at.desc())
                    .limit(1)
                )
                row = result.scalar_one_or_none()
            if row is None:
                return None
            return self._row_to_dict(row)

    async def add_credential(
        self,
        credential_value: str,
        provider_id: str,
        source: str = "manual",
    ) -> dict:
        """Add a new credential.

        The credential value is stored in the SecretStore. Only metadata
        and a masked representation are stored in the database.

        Args:
            credential_value: The raw API key or token.
            provider_id: The provider this credential belongs to.
            source: How the credential was obtained ('manual', 'provider-assisted').

        Returns:
            Safe metadata dict for the created credential.

        Raises:
            ValueError: If the credential value is empty.
        """
        if not credential_value or not credential_value.strip():
            raise ValueError("Credential value cannot be empty")

        credential_value = credential_value.strip()

        # Store the secret
        secret_ref = self._secret_store.store(credential_value)
        key_masked = mask_secret(credential_value)

        async with get_async_session() as session:
            row = await CredentialRepository.create(
                session,
                provider_id=provider_id,
                key_masked=key_masked,
                secret_ref=secret_ref,
                source=source,
            )
            # New credentials start as inactive — activation is explicit
            await CredentialRepository.update_fields(
                session, row.id, state="inactive",
            )
            row.state = "inactive"

            # Record event
            await CredentialEventRepository.create(
                session,
                event_type="imported_manually",
                status="success",
                provider_id=provider_id,
                credential_id=row.id,
                details_json=f'{{"source":"{source}","masked":"{key_masked}"}}',
            )

            await session.commit()

            logger.info(
                "Credential added: id=%s provider=%s masked=%s",
                row.id, provider_id, key_masked,
            )

            return self._row_to_dict(row)

    async def validate_credential(self, credential_id: str) -> dict:
        """Validate a credential.

        Uses the provider-validation adapter abstraction. Currently,
        without a provider registry, validation returns 'unknown' status.

        The validation adapter pattern allows future provider-specific
        implementations without changing the CredentialManager.

        Returns:
            Updated credential metadata dict.
        """
        async with get_async_session() as session:
            row = await CredentialRepository.get_by_id(session, credential_id)
            if row is None:
                raise ValueError(f"Credential not found: {credential_id}")

            # Perform validation through the adapter abstraction
            validation_result = await self._perform_validation(row)

            # Update the credential record
            now = _utcnow()
            await CredentialRepository.update_fields(
                session,
                credential_id,
                validation_status=validation_result["status"],
                last_validated=now,
                last_validation_error=validation_result.get("error"),
            )

            # Record event
            await CredentialEventRepository.create(
                session,
                event_type="validated",
                status="success" if validation_result["status"] == "valid" else "failed",
                provider_id=row.provider_id,
                credential_id=credential_id,
                failure_reason=validation_result.get("error"),
                details_json=f'{{"validation_status":"{validation_result["status"]}"}}',
            )

            await session.commit()

            logger.info(
                "Credential validated: id=%s status=%s",
                credential_id, validation_result["status"],
            )

            # Re-fetch to get updated fields
            row = await CredentialRepository.get_by_id(session, credential_id)
            return self._row_to_dict(row)

    async def activate_credential(self, credential_id: str) -> dict:
        """Activate a credential.

        Steps:
        1. Validate that the credential exists
        2. Deactivate any currently active credential for the same provider
        3. Mark this credential as active
        4. Write the credential to active_key.txt via the legacy adapter
        5. Record events

        The legacy gateway will discover the new key on its next reload
        cycle (every 3 seconds). No gateway restart is needed.
        """
        async with get_async_session() as session:
            row = await CredentialRepository.get_by_id(session, credential_id)
            if row is None:
                raise ValueError(f"Credential not found: {credential_id}")

            # Deactivate any currently active credential for this provider
            current_active = await CredentialRepository.get_active(session, row.provider_id)
            if current_active and current_active.id != credential_id:
                await CredentialRepository.update_fields(
                    session,
                    current_active.id,
                    state="inactive",
                    deactivated_at=_utcnow(),
                )
                await CredentialEventRepository.create(
                    session,
                    event_type="deactivated",
                    status="success",
                    provider_id=current_active.provider_id,
                    credential_id=current_active.id,
                    details_json='{"reason":"replaced_by_new_activation"}',
                )
                logger.info(
                    "Deactivated previous credential: id=%s",
                    current_active.id,
                )

            # Activate the new credential
            now = _utcnow()
            await CredentialRepository.update_fields(
                session,
                credential_id,
                state="active",
                activated_at=now,
                deactivated_at=None,
            )

            await CredentialEventRepository.create(
                session,
                event_type="activated",
                status="success",
                provider_id=row.provider_id,
                credential_id=credential_id,
            )

            await session.commit()

        # Write to active_key.txt (outside the DB session)
        secret_value = self._secret_store.retrieve(row.secret_ref)
        if secret_value:
            try:
                self._legacy_adapter.write_active_credential(secret_value)
            except Exception as e:
                logger.error("Failed to write active credential to legacy store: %s", e)
                # Don't fail the activation — the DB state is correct.
                # The user can retry or fix the file manually.
        else:
            logger.warning(
                "Secret not found for credential %s — legacy store not updated",
                credential_id,
            )

        logger.info("Credential activated: id=%s", credential_id)

        # Re-fetch to return updated state
        async with get_async_session() as session:
            row = await CredentialRepository.get_by_id(session, credential_id)
            return self._row_to_dict(row)

    async def deactivate_credential(self, credential_id: str) -> dict:
        """Deactivate a credential.

        Marks the credential as inactive and clears active_key.txt
        if this was the active credential.
        """
        async with get_async_session() as session:
            row = await CredentialRepository.get_by_id(session, credential_id)
            if row is None:
                raise ValueError(f"Credential not found: {credential_id}")

            was_active = row.state == "active"

            await CredentialRepository.update_fields(
                session,
                credential_id,
                state="inactive",
                deactivated_at=_utcnow(),
            )

            await CredentialEventRepository.create(
                session,
                event_type="deactivated",
                status="success",
                provider_id=row.provider_id,
                credential_id=credential_id,
            )

            await session.commit()

        # Clear active_key.txt if this was the active credential
        if was_active:
            try:
                self._legacy_adapter.clear_active_credential()
                logger.info("Cleared active_key.txt (credential %s deactivated)", credential_id)
            except Exception as e:
                logger.error("Failed to clear active_key.txt: %s", e)

        logger.info("Credential deactivated: id=%s", credential_id)

        async with get_async_session() as session:
            row = await CredentialRepository.get_by_id(session, credential_id)
            return self._row_to_dict(row)

    async def replace_credential(
        self,
        new_credential_value: str,
        provider_id: str,
    ) -> dict:
        """Replace the active credential with a new one.

        This is the explicit user-initiated replacement workflow:
        1. Add the new credential
        2. Deactivate the current active credential (if any)
        3. Activate the new credential
        4. Record replacement events

        The previous credential is deactivated, not deleted.
        """
        if not new_credential_value or not new_credential_value.strip():
            raise ValueError("Credential value cannot be empty")

        # Record replacement requested
        async with get_async_session() as session:
            current_active = await CredentialRepository.get_active(session, provider_id)
            if current_active:
                await CredentialEventRepository.create(
                    session,
                    event_type="replacement_requested",
                    status="success",
                    provider_id=provider_id,
                    credential_id=current_active.id,
                )
            await session.commit()

        # Add the new credential
        new_cred = await self.add_credential(
            new_credential_value,
            provider_id=provider_id,
            source="manual",
        )

        # Activate it (this deactivates the previous one automatically)
        result = await self.activate_credential(new_cred["id"])

        # Record replacement completed
        async with get_async_session() as session:
            await CredentialEventRepository.create(
                session,
                event_type="replacement_completed",
                status="success",
                provider_id=provider_id,
                credential_id=new_cred["id"],
                details_json=f'{{"new_credential_id":"{new_cred["id"]}"}}',
            )
            await session.commit()

        logger.info("Credential replaced for provider %s: new id=%s", provider_id, new_cred["id"])
        return result

    # ── Internal ─────────────────────────────────────────────────

    async def _perform_validation(self, row: CredentialRow) -> dict:
        """Perform credential validation through the adapter abstraction.

        Currently returns 'unknown' because we don't have a provider
        registry or provider-specific validation adapters yet.

        Future phases will implement provider-specific validation:
        - For API keys: make a lightweight API call to verify the key
        - For session cookies: check if the session is still valid
        - For OAuth tokens: check expiration

        Returns:
            Dict with 'status' and optional 'error' keys.
        """
        # Check if the secret still exists in the store
        if not row.secret_ref:
            return {"status": "invalid", "error": "No secret reference"}

        secret_exists = self._secret_store.exists(row.secret_ref)
        if not secret_exists:
            return {"status": "invalid", "error": "Secret not found in store"}

        # Without a provider-specific adapter, we cannot validate against
        # the upstream provider. Return 'unknown' rather than pretending
        # validation succeeded.
        #
        # This is the honest answer: we know the credential is stored,
        # but we don't know if it's still valid with the provider.
        return {"status": "unknown"}

    def _row_to_dict(self, row: CredentialRow) -> dict:
        """Convert a CredentialRow to a safe API dict (no raw secrets)."""
        return {
            "id": row.id,
            "provider_id": row.provider_id,
            "key_masked": row.key_masked,
            "source": row.source,
            "state": row.state,
            "validation_status": row.validation_status,
            "last_validated": row.last_validated,
            "last_validation_error": row.last_validation_error,
            "usage_input": row.usage_input,
            "usage_output": row.usage_output,
            "usage_total": row.usage_total,
            "activated_at": row.activated_at,
            "deactivated_at": row.deactivated_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


# Module-level singleton
_credential_manager: Optional[CredentialManager] = None


def get_credential_manager() -> CredentialManager:
    """Return the singleton CredentialManager instance."""
    global _credential_manager
    if _credential_manager is None:
        _credential_manager = CredentialManager()
    return _credential_manager


def set_credential_manager(manager: CredentialManager) -> None:
    """Override the credential manager (for testing)."""
    global _credential_manager
    _credential_manager = manager
