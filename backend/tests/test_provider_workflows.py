"""
Tests for user-initiated provider key workflows (list/create/revoke/
import-latest) and the Opus dashboard adapter.

The dashboard is simulated with httpx.MockTransport — no real network.
All workflows are capability-gated and session-gated.
"""

import json
import time
import uuid

import httpx
import pytest
from httpx import AsyncClient, ASGITransport


class FakeDashboard:
    """In-memory dashboard mimicking the legacy Opus dashboard API."""

    def __init__(self):
        self.keys = []
        self.quota = 0
        self.quota_limit = 10_000
        self.session_secret = "session-secret-123"
        self.calls = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append({"method": request.method, "path": request.url.path})
        cookie = request.headers.get("cookie", "")
        if f"opus_session={self.session_secret}" not in cookie:
            return httpx.Response(401, json={"error": "unauthorized"})

        path = request.url.path
        if request.method == "GET" and path.endswith("/dashboard/api/keys"):
            return httpx.Response(200, json=self.keys)
        if request.method == "POST" and path.endswith("/dashboard/api/keys"):
            if self.quota >= self.quota_limit:
                return httpx.Response(400, json={"error": "Not enough tokens"})
            body = json.loads(request.content)
            key = {"id": str(uuid.uuid4().hex[:12]), "name": body.get("name"),
                   "key": f"newkey-{uuid.uuid4().hex[:16]}", "dailyTokenLimit": body.get("dailyTokenLimit"),
                   "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            self.keys.append(key)
            self.quota += body.get("dailyTokenLimit", 0)
            return httpx.Response(201, json=key)
        if request.method == "DELETE" and "/dashboard/api/keys/" in path:
            key_id = path.rsplit("/", 1)[-1]
            self.keys = [k for k in self.keys if k["id"] != key_id]
            return httpx.Response(204)
        return httpx.Response(404, json={"error": "not found"})


@pytest.fixture(autouse=True)
def _configure_for_test(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("GCC_DATABASE_PATH", db_path)
    monkeypatch.setenv("GCC_DEMO_PROVIDER_ENABLED", "false")

    from app.core.config import get_settings
    get_settings.cache_clear()

    import app.storage.database as db_mod
    db_mod._engine = None
    db_mod._session_factory = None

    import app.core.secrets as secrets_mod
    secrets_mod._store = None

    import app.services.credential_manager as cm
    cm._credential_manager = None
    import app.services.credential_health_manager as chm
    chm._health_manager = None
    import app.services.session_manager as sm
    sm._session_manager = None
    import app.services.provider_workflows as pw
    pw._service = None
    import app.adapters.session_provider_adapter as spa
    spa._adapter = None

    yield

    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._session_factory = None
    secrets_mod._store = None
    cm._credential_manager = None
    chm._health_manager = None
    sm._session_manager = None
    pw._service = None
    spa._adapter = None


@pytest.fixture
async def app():
    from app.main import create_app
    from app.storage.database import init_database
    app = create_app()
    await init_database()
    return app


@pytest.fixture
async def client(app):
    from app.storage.database import close_database
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_database()


CAPABILITIES = {
    "credential_validation": True,
    "credential_discovery": True,
    "credential_creation": True,
    "credential_revocation": True,
    "session_required": True,
    "session_validation": "opus-dashboard",
    "dashboard_adapter": "opus-dashboard",
}


async def seed_dashboard_provider(monkeypatch, dashboard: FakeDashboard) -> str:
    """Create a provider with an active session + credential, wired to the mock."""
    from app.core.secrets import get_secret_store
    from app.storage.database import get_async_session
    from app.storage.repositories import (
        ProviderRepository, SessionRepository, CredentialRepository, ModelRepository)
    from app.storage.models import _utcnow

    async with get_async_session() as session:
        await ProviderRepository.create(
            session, name="Opus", protocol="openai-completions",
            base_url="http://dashboard", auth_type="session-cookie",
            provider_id="opus",
        )
        await ProviderRepository.update_fields(
            session, "opus", capabilities_json=json.dumps(CAPABILITIES),
            metadata_json=json.dumps({"session_cookie_name": "opus_session"}))

        # Active session with the dashboard's expected secret
        ref = get_secret_store().store(dashboard.session_secret)
        sess = await SessionRepository.create(
            session, provider_id="opus", session_masked="****cret", secret_ref=ref)
        await SessionRepository.update_fields(
            session, sess.id, lifecycle_state="active", activated_at=_utcnow())

        # Active credential (a key the dashboard doesn't know — used for
        # comparing during import-latest)
        cred_ref = get_secret_store().store("oldkey-abcdef")
        cred = await CredentialRepository.create(
            session, provider_id="opus", key_masked="****cdef", secret_ref=cred_ref)
        await CredentialRepository.update_fields(
            session, cred.id, state="active", activated_at=_utcnow())

        await ModelRepository.create(
            session, provider_id="opus", display_name="opus-model", model_id="opus-model")
        await session.commit()
    return "opus"


def install_dashboard(monkeypatch, dashboard: FakeDashboard):
    import httpx as _httpx
    original_client = _httpx.AsyncClient
    monkeypatch.setattr(_httpx, "AsyncClient", lambda *a, **k: original_client(
        transport=_httpx.MockTransport(dashboard), *a, **k))


# ── Tests ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_keys_masked(client, monkeypatch):
    dashboard = FakeDashboard()
    dashboard.keys = [{"id": "k1", "name": "main", "key": "super-secret-key-value",
                       "dailyTokenLimit": 1000, "createdAt": "2026-01-01T00:00:00Z"}]
    await seed_dashboard_provider(monkeypatch, dashboard)
    install_dashboard(monkeypatch, dashboard)

    resp = await client.get("/api/providers/opus/keys")
    assert resp.status_code == 200
    keys = resp.json()["keys"]
    assert len(keys) == 1
    # Secrets are masked, never returned
    assert "super-secret-key-value" not in resp.text
    assert keys[0]["masked"].endswith("alue")


@pytest.mark.asyncio
async def test_create_key_stored_as_credential(client, monkeypatch):
    dashboard = FakeDashboard()
    await seed_dashboard_provider(monkeypatch, dashboard)
    install_dashboard(monkeypatch, dashboard)

    resp = await client.post("/api/providers/opus/keys", json={
        "name": "manual", "daily_limit": 2000})
    assert resp.status_code == 200
    data = resp.json()
    assert data["created"] is True
    assert data["credential"]["source"] == "provider-assisted"
    assert data["credential"]["state"] == "inactive"  # never auto-activated
    new_key = dashboard.keys[-1]["key"]
    assert new_key not in resp.text

    # The secret went to the SecretStore
    from app.core.secrets import get_secret_store
    from app.storage.database import get_async_session
    from app.storage.repositories import CredentialRepository
    async with get_async_session() as session:
        cred = await CredentialRepository.get_by_id(session, data["credential"]["id"])
        assert get_secret_store().retrieve(cred.secret_ref) == new_key


@pytest.mark.asyncio
async def test_quota_full_is_surface_not_auto_delete(client, monkeypatch):
    dashboard = FakeDashboard()
    dashboard.quota = dashboard.quota_limit  # quota exhausted
    await seed_dashboard_provider(monkeypatch, dashboard)
    install_dashboard(monkeypatch, dashboard)

    resp = await client.post("/api/providers/opus/keys", json={
        "name": "x", "daily_limit": 100})
    assert resp.status_code == 502
    assert "quota" in resp.json()["detail"].lower()
    # No delete calls happened — keys are never deleted automatically
    methods = [c["method"] for c in dashboard.calls]
    assert "DELETE" not in methods


@pytest.mark.asyncio
async def test_revoke_key(client, monkeypatch):
    dashboard = FakeDashboard()
    dashboard.keys = [{"id": "k1", "name": "main", "key": "value-1",
                       "dailyTokenLimit": 100, "createdAt": "2026-01-01T00:00:00Z"}]
    await seed_dashboard_provider(monkeypatch, dashboard)
    install_dashboard(monkeypatch, dashboard)

    resp = await client.delete("/api/providers/opus/keys/k1")
    assert resp.status_code == 200
    assert resp.json()["revoked"] is True
    assert dashboard.keys == []


@pytest.mark.asyncio
async def test_import_latest_key_workflow(client, monkeypatch):
    dashboard = FakeDashboard()
    dashboard.keys = [
        {"id": "k1", "name": "old", "key": "oldkey-abcdef", "dailyTokenLimit": 100,
         "createdAt": "2026-01-01T00:00:00Z"},
        {"id": "k2", "name": "new", "key": "brand-new-key-123456", "dailyTokenLimit": 100,
         "createdAt": "2026-02-01T00:00:00Z"},
    ]
    await seed_dashboard_provider(monkeypatch, dashboard)
    install_dashboard(monkeypatch, dashboard)

    resp = await client.post("/api/providers/opus/keys/import-latest")
    assert resp.status_code == 200
    data = resp.json()
    assert data["changed"] is True
    assert data["credential"]["state"] == "active"
    assert data["masked"].endswith("3456")
    assert "brand-new-key-123456" not in resp.text

    # Running it again is a no-op (already latest) — no new credential
    resp2 = await client.post("/api/providers/opus/keys/import-latest")
    assert resp2.json()["changed"] is False


@pytest.mark.asyncio
async def test_workflows_require_capability(client):
    """A provider without dashboard capabilities cannot use key workflows."""
    from app.storage.database import get_async_session
    from app.storage.repositories import ProviderRepository
    async with get_async_session() as session:
        await ProviderRepository.create(
            session, name="Plain", protocol="openai-completions",
            base_url="https://x", auth_type="api-key", provider_id="plain")
        await session.commit()

    resp = await client.get("/api/providers/plain/keys")
    assert resp.status_code == 403
    resp = await client.post("/api/providers/plain/keys/import-latest")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_session_validation_via_adapter(client, monkeypatch):
    """Session validation uses the declared dashboard adapter (200 → valid)."""
    dashboard = FakeDashboard()
    await seed_dashboard_provider(monkeypatch, dashboard)
    install_dashboard(monkeypatch, dashboard)

    from app.storage.database import get_async_session
    from app.storage.repositories import SessionRepository
    async with get_async_session() as session:
        sess = (await SessionRepository.list_all(session))[0]
        sid = sess.id

    resp = await client.post(f"/api/sessions/{sid}/validate")
    assert resp.status_code == 200
    data = resp.json()["session"]
    assert data["validation_state"] == "valid"
    assert data["health"] == "healthy"


@pytest.mark.asyncio
async def test_session_validation_invalid_cookie(client, monkeypatch):
    """A wrong session secret → dashboard 401 → invalid."""
    dashboard = FakeDashboard()
    await seed_dashboard_provider(monkeypatch, dashboard)
    install_dashboard(monkeypatch, dashboard)

    # Corrupt the stored secret
    from app.core.secrets import get_secret_store
    from app.storage.database import get_async_session
    from app.storage.repositories import SessionRepository
    async with get_async_session() as session:
        sess = (await SessionRepository.list_all(session))[0]
        sid = sess.id
        get_secret_store().delete(sess.secret_ref)
        get_secret_store().store("wrong-secret")
        new_ref = get_secret_store().store("wrong-secret")
        await SessionRepository.update_fields(session, sid, secret_ref=new_ref)
        await session.commit()

    resp = await client.post(f"/api/sessions/{sid}/validate")
    assert resp.status_code == 200
    data = resp.json()["session"]
    assert data["validation_state"] == "invalid"
    assert data["health"] == "critical"
    assert data["lifecycle_state"] == "invalid"
