"""
ProviderWorkflowService — user-initiated, provider-specific credential
workflows (list / create / revoke keys, import the latest key).

Every operation here is explicit: triggered by the user through the UI
or API, capability-gated by the provider's declared capabilities, and
session-gated (an active session is required when the provider declares
session_required). NOTHING runs automatically — there is no polling, no
background key rotation, and no quota-recovery automation.

The 'import latest key' workflow:
    fetch newest dashboard key → compare with active credential →
    if different: store as new credential (source=provider-assisted),
    validate it, activate it (previous credential becomes inactive)
    if same: report no change (no new credential row, no churn)
"""

from __future__ import annotations

import json
from typing import Optional

from app.core.logging import get_logger, mask_secret
from app.storage.database import get_async_session
from app.storage.repositories import ProviderRepository, CredentialRepository, CredentialEventRepository
from app.adapters.registry import get_dashboard_adapter
from app.services.credential_manager import CredentialManager

logger = get_logger("provider_workflows")


class ProviderWorkflowService:
    """User-initiated provider key management."""

    def __init__(self) -> None:
        self._credential_manager = CredentialManager()

    # ── Helpers ──────────────────────────────────────────────────

    async def _get_adapter(self, provider_id: str):
        async with get_async_session() as session:
            provider = await ProviderRepository.get_by_id(session, provider_id)
            if provider is None:
                raise ValueError(f"Provider not found: {provider_id}")
            capabilities = self._capabilities(provider)
            adapter_name = capabilities.get("dashboard_adapter")
            if not adapter_name:
                raise PermissionError(
                    f"Provider '{provider.name}' does not support dashboard key management"
                )
        adapter = get_dashboard_adapter(provider)
        if adapter is None:
            raise PermissionError(
                f"Provider '{provider.name}' has no dashboard adapter registered"
            )
        return provider, adapter

    @staticmethod
    def _capabilities(provider) -> dict:
        try:
            return json.loads(provider.capabilities_json or "{}") or {}
        except (ValueError, TypeError):
            return {}

    async def _check_capability(self, provider, capability: str) -> None:
        capabilities = self._capabilities(provider)
        if not capabilities.get(capability):
            raise PermissionError(
                f"Provider '{provider.name}' does not declare capability '{capability}'"
            )

    # ── Key workflows ────────────────────────────────────────────

    async def list_keys(self, provider_id: str) -> list[dict]:
        provider, adapter = await self._get_adapter(provider_id)
        await self._check_capability(provider, "credential_discovery")
        keys = await adapter.list_keys()
        # Return safe metadata only — full key values are masked
        return [
            {
                "id": k.get("id"),
                "name": k.get("name"),
                "masked": mask_secret(k.get("key") or k.get("apiKey") or "") or None,
                "createdAt": k.get("createdAt"),
                "dailyTokenLimit": k.get("dailyTokenLimit"),
            }
            for k in keys
        ]

    async def create_key(self, provider_id: str, name: str, daily_limit: int) -> dict:
        provider, adapter = await self._get_adapter(provider_id)
        await self._check_capability(provider, "credential_creation")
        if not name or not name.strip():
            raise ValueError("Key name cannot be empty")
        if daily_limit <= 0:
            raise ValueError("daily_limit must be positive")

        created = await adapter.create_key(name.strip(), daily_limit)
        new_key = created["key"]

        # Store the new key as a credential (provider-assisted, inactive
        # until the user validates/activates — never auto-activated).
        cred = await self._credential_manager.add_credential(
            credential_value=new_key,
            provider_id=provider_id,
            source="provider-assisted",
        )
        logger.info(
            "Provider-assisted credential created: provider=%s credential=%s masked=%s",
            provider_id, cred["id"], cred["key_masked"],
        )
        return {
            "created": True,
            "credential": cred,
            "masked": cred["key_masked"],
        }

    async def revoke_key(self, provider_id: str, key_id: str) -> dict:
        provider, adapter = await self._get_adapter(provider_id)
        await self._check_capability(provider, "credential_revocation")
        deleted = await adapter.delete_key(key_id)
        if not deleted:
            raise RuntimeError(f"Provider failed to revoke key {key_id}")
        logger.info("Provider key revoked: provider=%s key_id=%s", provider_id, key_id)
        return {"revoked": True, "key_id": key_id}

    async def import_latest_key(self, provider_id: str) -> dict:
        """Fetch the newest dashboard key and import it if it differs from
        the active credential (legacy pull_latest_key workflow, but
        user-initiated and never touching active_key.txt directly)."""
        provider, adapter = await self._get_adapter(provider_id)
        await self._check_capability(provider, "credential_discovery")

        latest = await adapter.fetch_latest_key()
        if latest is None:
            raise RuntimeError("Provider returned no keys")

        new_key = latest["key"]

        # Compare with the active credential
        active_value = None
        async with get_async_session() as session:
            active = await CredentialRepository.get_active(session, provider_id)
            if active and active.secret_ref:
                from app.core.secrets import get_secret_store
                active_value = get_secret_store().retrieve(active.secret_ref)

        if active_value and active_value.strip() == new_key:
            return {"changed": False, "message": "The active credential is already the latest key"}

        # Store + validate + activate (explicit user request)
        cred = await self._credential_manager.add_credential(
            credential_value=new_key,
            provider_id=provider_id,
            source="provider-assisted",
        )
        validated = await self._credential_manager.validate_credential(cred["id"])
        if validated["validation_status"] in ("invalid", "expired"):
            raise RuntimeError(
                f"Imported key failed validation ({validated['validation_status']}) — "
                f"it was stored but not activated"
            )
        activated = await self._credential_manager.activate_credential(cred["id"])

        async with get_async_session() as session:
            await CredentialEventRepository.create(
                session,
                event_type="replacement_completed",
                status="success",
                provider_id=provider_id,
                credential_id=cred["id"],
                details_json=json.dumps({"source": "provider_import"}),
            )
            await session.commit()

        logger.info(
            "Latest key imported: provider=%s credential=%s",
            provider_id, cred["id"],
        )
        return {
            "changed": True,
            "credential": activated,
            "masked": activated["key_masked"],
        }


# Module-level singleton
_service: Optional[ProviderWorkflowService] = None


def get_provider_workflow_service() -> ProviderWorkflowService:
    global _service
    if _service is None:
        _service = ProviderWorkflowService()
    return _service


def set_provider_workflow_service(service: ProviderWorkflowService) -> None:
    global _service
    _service = service
