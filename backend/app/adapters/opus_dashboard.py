"""
OpusDashboardAdapter — provider-specific dashboard integration for
providers that expose the legacy Opus dashboard API.

Covers two responsibilities, both USER-INITIATED ONLY:

1. Session validation (SessionValidator protocol):
   GET {base_url}/dashboard/api/keys with the session cookie.
   - 200                → valid
   - 401/403            → invalid (rejected credentials)
   - other non-200      → invalid (cannot verify) with the status code
   - network errors     → unavailable

2. Key management operations:
   - list_keys()
   - create_key(name, daily_limit)
   - delete_key(key_id)
   - fetch_latest_key()

Endpoints and semantics come from the legacy scripts (KeyBinder.py,
rotate_now.py, pull_latest_key.py) — see docs/legacy-session-flow.md.
The session secret is obtained through the SessionProviderAdapter and is
NEVER logged, stored, or returned.

Policy differences from legacy (monitor-first):
- The quota-full "delete all keys and retry" behavior is NOT automatic.
  The adapter surfaces a clear error instead; the user decides.
- TLS certificate verification is ENABLED by default (the legacy scripts
  disabled it). Set provider metadata {"verify_tls": false} to opt out.
- Nothing here runs without an explicit user action.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Optional

import httpx

from app.core.logging import get_logger
from app.services.session_manager import (
    SessionValidationResult,
    SessionValidator,
)
from app.adapters.session_provider_adapter import get_session_provider_adapter

logger = get_logger("opus_dashboard")

ADAPTER_NAME = "opus-dashboard"

# Dashboard key endpoints (from the legacy scripts)
KEYS_PATH = "/dashboard/api/keys"


class OpusDashboardAdapter:
    """Talks to the legacy-style Opus dashboard API on behalf of a provider."""

    def __init__(self, provider) -> None:
        # provider is a ProviderRow (persistence model, no session attached)
        self._provider = provider
        # The dashboard may live at a different base than the inference API.
        # metadata.dashboard_base_url overrides provider.base_url.
        metadata = self._metadata()
        dashboard_base = metadata.get("dashboard_base_url") or provider.base_url
        self._base_url = (dashboard_base or "").rstrip("/")

    # ── Config helpers ───────────────────────────────────────────

    def _cookie_name(self) -> str:
        metadata = self._metadata()
        return str((metadata or {}).get("session_cookie_name") or "opus_session")

    def _metadata(self) -> dict:
        try:
            return json.loads(self._provider.metadata_json or "{}")
        except (ValueError, TypeError):
            return {}

    def _verify_tls(self) -> bool:
        return bool((self._metadata() or {}).get("verify_tls", True))

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=5.0),
            follow_redirects=False,
            verify=self._verify_tls(),
        )

    def _headers(self) -> dict:
        return {
            "User-Agent": "Gateway-Control-Center/1.0",
            "Content-Type": "application/json",
            "Accept": "*/*",
        }

    # ── Session validation ───────────────────────────────────────

    async def validate(
        self,
        session,
        secret_value: Optional[str] = None,
    ) -> SessionValidationResult:
        """Validate the session against the dashboard's key-list endpoint."""
        if not secret_value:
            # Try the provider adapter (active session) as a fallback
            secret_value = await get_session_provider_adapter().get_active_session_secret(
                self._provider.id
            )
        if not secret_value:
            return SessionValidationResult(status="invalid", error="No session secret available")

        url = f"{self._base_url}{KEYS_PATH}"
        try:
            async with self._client() as client:
                resp = await client.get(
                    url,
                    headers=self._headers(),
                    cookies={self._cookie_name(): secret_value},
                )
        except httpx.HTTPError as e:
            return SessionValidationResult(status="unavailable", error=f"Dashboard unreachable: {e}")

        if resp.status_code == 200:
            return SessionValidationResult(status="valid")
        if resp.status_code in (401, 403):
            return SessionValidationResult(
                status="invalid",
                error=f"Dashboard rejected the session (HTTP {resp.status_code})",
            )
        return SessionValidationResult(
            status="invalid",
            error=f"Dashboard returned HTTP {resp.status_code}",
        )

    # ── Key operations (user-initiated only) ─────────────────────

    async def _session_secret(self) -> str:
        secret = await get_session_provider_adapter().get_active_session_secret(
            self._provider.id
        )
        if not secret:
            raise PermissionError(
                f"Provider '{self._provider.name}' has no active session — "
                f"add/activate one in the Sessions page"
            )
        return secret

    async def list_keys(self) -> list[dict]:
        secret = await self._session_secret()
        url = f"{self._base_url}{KEYS_PATH}"
        async with self._client() as client:
            resp = await client.get(
                url,
                headers=self._headers(),
                cookies={self._cookie_name(): secret},
            )
        if resp.status_code != 200:
            raise RuntimeError(f"List keys failed: HTTP {resp.status_code}")
        data = resp.json()
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("keys", data.get("data", []))
        return []

    async def create_key(self, name: str, daily_limit: int) -> dict:
        """Create a key. Does NOT auto-delete on quota-full (legacy did)."""
        secret = await self._session_secret()
        url = f"{self._base_url}{KEYS_PATH}"
        async with self._client() as client:
            resp = await client.post(
                url,
                headers=self._headers(),
                cookies={self._cookie_name(): secret},
                json={"name": name, "dailyTokenLimit": daily_limit},
            )
        if resp.status_code in (200, 201):
            data = resp.json()
            new_key = (
                data.get("key") or data.get("apiKey") or data.get("token")
                or (data.get("data") or {}).get("key") or (data.get("data") or {}).get("apiKey")
            )
            if not new_key:
                raise RuntimeError("Key field not found in the provider response")
            return {"key": new_key, "response": data}
        if resp.status_code == 400 and "Not enough tokens" in resp.text:
            raise RuntimeError(
                "Provider quota is full. Revoke unused keys on the provider "
                "(Keys section) and retry — keys are never deleted automatically."
            )
        raise RuntimeError(f"Create key failed: HTTP {resp.status_code}: {resp.text[:200]}")

    async def delete_key(self, key_id: str) -> bool:
        secret = await self._session_secret()
        url = f"{self._base_url}{KEYS_PATH}/{key_id}"
        async with self._client() as client:
            resp = await client.delete(
                url,
                headers=self._headers(),
                cookies={self._cookie_name(): secret},
            )
        return resp.status_code in (200, 204)

    async def fetch_latest_key(self) -> Optional[dict]:
        """Return the newest key from the dashboard (legacy pull_latest_key logic)."""
        keys = await self.list_keys()
        if not keys:
            return None

        def sort_key(k):
            created = k.get("createdAt")
            if created:
                try:
                    return datetime.fromisoformat(created.replace("Z", "+00:00"))
                except Exception:
                    pass
            return str(k.get("id", ""))

        latest = max(keys, key=sort_key)
        key_value = latest.get("key") or latest.get("apiKey") or latest.get("token")
        if not key_value:
            return None
        return {"key": key_value.strip(), "meta": latest}


def make_opus_dashboard_validator(provider) -> SessionValidator:
    """Build a SessionValidator for a provider (registry factory)."""
    adapter = OpusDashboardAdapter(provider)
    # Wrap the adapter's validate into a validator instance
    class _Validator:
        def __init__(self, inner):
            self._inner = inner

        async def validate(self, session, secret_value=None) -> SessionValidationResult:
            return await self._inner.validate(session, secret_value=secret_value)

    return _Validator(adapter)
