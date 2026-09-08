"""
ProviderManager — business-logic owner of provider configuration.

Operations:
- CRUD for providers (name, protocol, base_url, auth_type, capabilities)
- Declared capabilities (explicit, not guessed):
    credential_validation   bool   — adapter can validate credentials
    credential_discovery    bool   — can list existing credentials/keys
    credential_creation     bool   — can create credentials/keys
    credential_revocation   bool   — can revoke/delete credentials/keys
    session_required        bool   — provider-side management needs a session
    session_validation      str    — session validator adapter name ("" = none)
    dashboard_adapter       str    — management adapter name ("" = none)
- Health checks: simple HTTP reachability probe of base_url
- Model discovery: OpenAI-compatible GET /models import (works for
  OpenRouter, OpenAI, Grok and any OpenAI-compatible endpoint)
- Quick-add template for OpenRouter

Monitor-first policy: nothing here acts on the provider's systems;
capabilities only declare what user-initiated actions are possible.
"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

import httpx

from app.core.logging import get_logger
from app.core.secrets import get_secret_store
from app.storage.database import get_async_session
from app.storage.models import ProviderRow, _utcnow
from app.storage.repositories import (
    ProviderRepository,
    ModelRepository,
    CredentialRepository,
)

logger = get_logger("provider_manager")

VALID_PROTOCOLS = ("openai-completions", "anthropic-messages")
VALID_AUTH_TYPES = ("api-key", "session-cookie")

KNOWN_CAPABILITIES = (
    "credential_validation",
    "credential_discovery",
    "credential_creation",
    "credential_revocation",
    "session_required",
    "session_validation",
    "dashboard_adapter",
)


class ProviderManager:
    """Provider business logic."""

    # ── Public API ───────────────────────────────────────────────

    async def list_providers(self) -> list[dict]:
        async with get_async_session() as session:
            rows = await ProviderRepository.list_all(session)
            return [self._row_to_dict(r) for r in rows]

    async def get_provider(self, provider_id: str) -> Optional[dict]:
        async with get_async_session() as session:
            row = await ProviderRepository.get_by_id(session, provider_id)
            if row is None:
                return None
            return self._row_to_dict(row)

    async def create_provider(
        self,
        name: str,
        protocol: str,
        base_url: str,
        auth_type: str = "api-key",
        capabilities: Optional[dict] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        if not name or not name.strip():
            raise ValueError("Provider name cannot be empty")
        if protocol not in VALID_PROTOCOLS:
            raise ValueError(f"Unsupported protocol '{protocol}'. Use one of: {', '.join(VALID_PROTOCOLS)}")
        if auth_type not in VALID_AUTH_TYPES:
            raise ValueError(f"Unsupported auth_type '{auth_type}'")
        if not base_url or not base_url.strip():
            raise ValueError("Provider base_url cannot be empty")

        capabilities = self._clean_capabilities(capabilities)
        async with get_async_session() as session:
            row = await ProviderRepository.create(
                session,
                name=name.strip(),
                protocol=protocol,
                base_url=base_url.strip().rstrip("/"),
                auth_type=auth_type,
            )
            if capabilities:
                await ProviderRepository.update_fields(
                    session, row.id, capabilities_json=json.dumps(capabilities))
            if metadata:
                await ProviderRepository.update_fields(
                    session, row.id, metadata_json=json.dumps(metadata))
            await session.commit()
            logger.info("Provider created: id=%s name=%s protocol=%s", row.id, row.name, row.protocol)
            return await self.get_provider(row.id)

    async def create_openrouter_provider(self, name: str = "OpenRouter") -> dict:
        """Quick-add an OpenRouter provider with the standard template."""
        return await self.create_provider(
            name=name,
            protocol="openai-completions",
            base_url="https://openrouter.ai/api/v1",
            auth_type="api-key",
            capabilities={
                "credential_validation": True,
                "credential_discovery": True,
                "credential_creation": False,
                "credential_revocation": False,
                "session_required": False,
                "session_validation": "",
                "dashboard_adapter": "",
            },
            metadata={"note": "OpenRouter — OpenAI-compatible aggregator", "homepage": "https://openrouter.ai"},
        )

    async def update_provider(self, provider_id: str, **fields) -> Optional[dict]:
        async with get_async_session() as session:
            row = await ProviderRepository.get_by_id(session, provider_id)
            if row is None:
                return None

            updates: dict[str, Any] = {}
            for key in ("name", "protocol", "base_url", "auth_type", "enabled"):
                if key in fields and fields[key] is not None:
                    value = fields[key]
                    if key == "protocol" and value not in VALID_PROTOCOLS:
                        raise ValueError(f"Unsupported protocol '{value}'")
                    if key == "auth_type" and value not in VALID_AUTH_TYPES:
                        raise ValueError(f"Unsupported auth_type '{value}'")
                    if key == "base_url":
                        value = str(value).strip().rstrip("/")
                    updates[key] = value
            if "capabilities" in fields:
                updates["capabilities_json"] = json.dumps(self._clean_capabilities(fields["capabilities"]))
            if "metadata" in fields:
                updates["metadata_json"] = json.dumps(fields["metadata"])

            await ProviderRepository.update_fields(session, provider_id, **updates)
            await session.commit()
            logger.info("Provider updated: id=%s", provider_id)
            return await self.get_provider(provider_id)

    async def delete_provider(self, provider_id: str) -> bool:
        """Delete a provider. Cascades to its models, credentials, sessions."""
        async with get_async_session() as session:
            row = await ProviderRepository.get_by_id(session, provider_id)
            if row is None:
                return False
            # Count dependents for the audit message
            models = await ModelRepository.list_by_provider(session, provider_id)
            creds = await CredentialRepository.list_by_provider(session, provider_id)
            await ProviderRepository.delete_by_id(session, provider_id)
            await session.commit()
            logger.info(
                "Provider deleted: id=%s name=%s (cascaded: %d models, %d credentials)",
                provider_id, row.name, len(models), len(creds),
            )
            return True

    async def check_health(self, provider_id: str) -> dict:
        """Probe the provider's base_url for reachability.

        Returns a health dict: status (healthy/unhealthy/unknown), latency_ms,
        http_status, error. This is a generic reachability probe only —
        it does not assert anything provider-specific.
        """
        async with get_async_session() as session:
            row = await ProviderRepository.get_by_id(session, provider_id)
            if row is None:
                raise ValueError(f"Provider not found: {provider_id}")

        start = time.monotonic()
        status = "unknown"
        http_status: Optional[int] = None
        error: Optional[str] = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False) as client:
                resp = await client.get(row.base_url)
                http_status = resp.status_code
                # Any HTTP response means the endpoint is reachable
                status = "healthy" if resp.status_code < 500 else "unhealthy"
        except httpx.HTTPError as e:
            status = "unhealthy"
            error = str(e)

        latency_ms = round((time.monotonic() - start) * 1000, 1)

        async with get_async_session() as session:
            await ProviderRepository.update_fields(
                session,
                provider_id,
                health_status=status,
                last_health_check=_utcnow(),
            )
            await session.commit()

        logger.info("Provider health check: id=%s status=%s", provider_id, status)
        return {
            "provider_id": provider_id,
            "status": status,
            "latency_ms": latency_ms,
            "http_status": http_status,
            "error": error,
        }

    async def discover_models(self, provider_id: str) -> dict:
        """Import models from an OpenAI-compatible GET /models endpoint.

        Uses the provider's active credential. Imported models are created
        (or updated) as disabled-by-default model rows so the user can
        review and enable them.

        Returns: {imported: n, skipped: n, models: [...]}
        """
        async with get_async_session() as session:
            provider = await ProviderRepository.get_by_id(session, provider_id)
            if provider is None:
                raise ValueError(f"Provider not found: {provider_id}")
            if provider.protocol != "openai-completions":
                raise ValueError("Model discovery requires an OpenAI-compatible provider")
            credential = await CredentialRepository.get_active(session, provider.id)
            if credential is None:
                raise ValueError("No active credential — activate one before model discovery")
            secret_ref = credential.secret_ref

        secret = get_secret_store().retrieve(secret_ref)
        if not secret:
            raise ValueError("Active credential has no stored secret")

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
                resp = await client.get(
                    f"{provider.base_url}/models",
                    headers={"Authorization": f"Bearer {secret}"},
                )
                if resp.status_code >= 400:
                    raise ValueError(f"Model discovery failed: HTTP {resp.status_code}")
                data = resp.json()
        except httpx.HTTPError as e:
            raise ValueError(f"Model discovery failed: {e}")

        items = data.get("data") or data if isinstance(data, list) else []
        imported = 0
        skipped = 0
        model_ids = []
        async with get_async_session() as session:
            existing = {m.model_id for m in await ModelRepository.list_by_provider(session, provider_id)}
            for item in items:
                model_id = item.get("id") if isinstance(item, dict) else str(item)
                if not model_id:
                    skipped += 1
                    continue
                if model_id in existing:
                    skipped += 1
                    continue
                row = await ModelRepository.create(
                    session,
                    provider_id=provider_id,
                    display_name=model_id,
                    model_id=model_id,
                    context_window=item.get("context_length") if isinstance(item, dict) else None,
                    capabilities=json.dumps(["chat", "completion"]),
                )
                # Imported models start disabled — the user reviews and enables
                await ModelRepository.update_fields(session, row.id, enabled=False)
                imported += 1
                model_ids.append(model_id)
            await session.commit()

        logger.info("Model discovery: provider=%s imported=%d skipped=%d", provider_id, imported, skipped)
        return {"imported": imported, "skipped": skipped, "models": model_ids}

    # ── Internal ─────────────────────────────────────────────────

    @staticmethod
    def _clean_capabilities(capabilities: Optional[dict]) -> dict:
        clean = {}
        if not capabilities:
            return clean
        for key in KNOWN_CAPABILITIES:
            if key in capabilities:
                clean[key] = capabilities[key]
        return clean

    @staticmethod
    def _parse_json(text: Optional[str]) -> Any:
        if not text:
            return None
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None

    def _row_to_dict(self, row: ProviderRow) -> dict:
        capabilities = self._parse_json(row.capabilities_json) or {}
        return {
            "id": row.id,
            "name": row.name,
            "protocol": row.protocol,
            "base_url": row.base_url,
            "auth_type": row.auth_type,
            "enabled": row.enabled,
            "health_status": row.health_status,
            "last_health_check": row.last_health_check,
            "capabilities": capabilities,
            "metadata": self._parse_json(row.metadata_json),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


# Module-level singleton
_manager: Optional[ProviderManager] = None


def get_provider_manager() -> ProviderManager:
    global _manager
    if _manager is None:
        _manager = ProviderManager()
    return _manager


def set_provider_manager(manager: ProviderManager) -> None:
    global _manager
    _manager = manager
