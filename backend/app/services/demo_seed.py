"""
Demo seed — idempotent setup of a local demo provider so the whole
system is testable without real credentials.

Creates (once):
- Provider "Demo Provider" (OpenAI-compatible, pointing at the local
  demo provider server; dashboard + session validation declared via the
  opus-dashboard adapter since the demo server mimics that API)
- A demo session secret (stored via SecretStore, never in the DB)
- A demo credential (active)
- Two demo models (demo-gpt-fast default, demo-gpt-pro fallback)

Runs at backend startup when GCC_DEMO_PROVIDER_ENABLED is true (default)
and the demo provider doesn't already exist.

The demo provider SERVER itself runs separately:
    python -m app.demo.demo_provider   (port 5900)
"""

from __future__ import annotations

import json
from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.secrets import get_secret_store
from app.storage.database import get_async_session
from app.storage.repositories import (
    ProviderRepository,
    ModelRepository,
    CredentialRepository,
    SessionRepository,
    SessionEventRepository,
)

logger = get_logger("demo_seed")

DEMO_PROVIDER_ID = "demo"
DEMO_SESSION_SECRET = "demo-session-secret"
DEMO_CREDENTIAL = "demo-key-00000000000000000001"


async def seed_demo() -> Optional[dict]:
    """Seed the demo provider setup. Idempotent."""
    settings = get_settings()
    if not settings.demo_provider_enabled:
        return None

    async with get_async_session() as session:
        existing = await ProviderRepository.get_by_id(session, DEMO_PROVIDER_ID)
        if existing is not None:
            return {"seeded": False, "reason": "demo provider already exists"}

        provider = await ProviderRepository.create(
            session,
            name="Demo Provider",
            protocol="openai-completions",
            base_url=f"{settings.demo_provider_url.rstrip('/')}/v1",
            auth_type="api-key",
            provider_id=DEMO_PROVIDER_ID,
        )
        await ProviderRepository.update_fields(
            session,
            DEMO_PROVIDER_ID,
            capabilities_json=json.dumps({
                "credential_validation": True,
                "credential_discovery": True,
                "credential_creation": True,
                "credential_revocation": True,
                "session_required": True,
                "session_validation": "opus-dashboard",
                "dashboard_adapter": "opus-dashboard",
            }),
            metadata_json=json.dumps({
                "session_cookie_name": "opus_session",
                "dashboard_base_url": settings.demo_provider_url.rstrip("/"),
                "note": "Local demo provider (mock server on port 5900)",
            }),
        )

        # Demo session (secret → SecretStore only)
        from app.storage.models import _utcnow
        secret_ref = get_secret_store().store(DEMO_SESSION_SECRET)
        sess = await SessionRepository.create(
            session,
            provider_id=DEMO_PROVIDER_ID,
            session_masked="demo-session-****",
            secret_ref=secret_ref,
            label="demo dashboard session",
            source="manual",
        )
        await SessionRepository.update_fields(
            session, sess.id, lifecycle_state="active", activated_at=_utcnow()
        )
        await SessionEventRepository.create(
            session,
            event_type="created",
            status="success",
            provider_id=DEMO_PROVIDER_ID,
            session_id=sess.id,
            details_json='{"source":"demo_seed"}',
        )

        # Demo credential (active)
        cred_secret_ref = get_secret_store().store(DEMO_CREDENTIAL)
        cred = await CredentialRepository.create(
            session,
            provider_id=DEMO_PROVIDER_ID,
            key_masked="demo-key-****…0001",
            secret_ref=cred_secret_ref,
            source="manual",
        )
        await CredentialRepository.update_fields(
            session, cred.id, state="active", activated_at=_utcnow()
        )

        # Demo models
        fast = await ModelRepository.create(
            session,
            provider_id=DEMO_PROVIDER_ID,
            display_name="Demo GPT Fast",
            model_id="demo-gpt-fast",
            context_window=128000,
            capabilities=json.dumps(["chat", "completion"]),
        )
        await ModelRepository.update_fields(
            session, fast.id, enabled=True, is_default=True
        )
        await ModelRepository.create(
            session,
            provider_id=DEMO_PROVIDER_ID,
            display_name="Demo GPT Pro",
            model_id="demo-gpt-pro",
            context_window=200000,
            capabilities=json.dumps(["chat", "completion"]),
        )
        await session.commit()

    logger.info(
        "Demo provider seeded: provider=%s (server at %s) — start it with "
        "'python -m app.demo.demo_provider'",
        DEMO_PROVIDER_ID, settings.demo_provider_url,
    )
    return {"seeded": True, "provider_id": DEMO_PROVIDER_ID}
