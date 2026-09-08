"""
Tests for ProviderManager, ModelManager, UsageManager and their API routes.

No real external services are called; provider reachability and model
discovery use httpx.MockTransport.
"""

import json

import httpx
import pytest
from httpx import AsyncClient, ASGITransport


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

    import app.services.provider_manager as pm
    pm._manager = None
    import app.services.model_manager as mm
    mm._manager = None
    import app.services.usage_manager as um
    um._manager = None

    yield

    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._session_factory = None
    secrets_mod._store = None
    pm._manager = None
    mm._manager = None
    um._manager = None


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


# ── Providers ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_create_list_get(client):
    resp = await client.post("/api/providers", json={
        "name": "Test Provider",
        "protocol": "openai-completions",
        "base_url": "https://api.example.com/v1",
        "auth_type": "api-key",
        "capabilities": {"credential_validation": True, "session_required": False},
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Test Provider"
    assert data["protocol"] == "openai-completions"
    assert data["capabilities"]["credential_validation"] is True
    pid = data["id"]

    listed = (await client.get("/api/providers")).json()
    assert listed["total"] == 1
    assert listed["providers"][0]["id"] == pid

    got = (await client.get(f"/api/providers/{pid}")).json()
    assert got["name"] == "Test Provider"


@pytest.mark.asyncio
async def test_provider_create_validation(client):
    resp = await client.post("/api/providers", json={
        "name": "Bad", "protocol": "not-a-protocol", "base_url": "https://x"})
    assert resp.status_code == 400

    resp = await client.post("/api/providers", json={
        "name": "", "protocol": "openai-completions", "base_url": "https://x"})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_provider_update_enable_disable(client):
    pid = (await client.post("/api/providers", json={
        "name": "P", "protocol": "openai-completions", "base_url": "https://x"})).json()["id"]

    resp = await client.put(f"/api/providers/{pid}", json={"enabled": False, "name": "P2"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["name"] == "P2"


@pytest.mark.asyncio
async def test_provider_delete(client):
    pid = (await client.post("/api/providers", json={
        "name": "P", "protocol": "openai-completions", "base_url": "https://x"})).json()["id"]
    resp = await client.delete(f"/api/providers/{pid}")
    assert resp.status_code == 200
    assert (await client.get(f"/api/providers/{pid}")).status_code == 404


@pytest.mark.asyncio
async def test_openrouter_quick_add(client):
    resp = await client.post("/api/providers/openrouter")
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "OpenRouter"
    assert data["base_url"] == "https://openrouter.ai/api/v1"
    assert data["protocol"] == "openai-completions"


@pytest.mark.asyncio
async def test_provider_health_check(client, monkeypatch):
    pid = (await client.post("/api/providers", json={
        "name": "P", "protocol": "openai-completions", "base_url": "http://upstream"})).json()["id"]

    async def handler(request):
        return httpx.Response(200, json={"ok": True})

    # Patch the AsyncClient used by ProviderManager.check_health
    import httpx as _httpx
    original = _httpx.AsyncClient

    def fake_client(*a, **k):
        return original(transport=_httpx.MockTransport(handler), *a, **k)

    monkeypatch.setattr(_httpx, "AsyncClient", fake_client)
    try:
        resp = await client.post(f"/api/providers/{pid}/check")
    finally:
        _httpx.AsyncClient = original

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["http_status"] == 200

    # Provider row health fields updated
    got = (await client.get(f"/api/providers/{pid}")).json()
    assert got["health_status"] == "healthy"
    assert got["last_health_check"] is not None


# ── Models ───────────────────────────────────────────────────────

async def _make_provider(client, name="P", protocol="openai-completions"):
    return (await client.post("/api/providers", json={
        "name": name, "protocol": protocol, "base_url": "https://x"})).json()["id"]


@pytest.mark.asyncio
async def test_model_crud(client):
    pid = await _make_provider(client)
    resp = await client.post("/api/models", json={
        "provider_id": pid, "display_name": "GPT-4o", "model_id": "gpt-4o",
        "context_window": 128000, "capabilities": ["chat"]})
    assert resp.status_code == 201
    model = resp.json()
    assert model["model_id"] == "gpt-4o"
    assert model["enabled"] is True

    listed = (await client.get("/api/models")).json()
    assert listed["total"] == 1

    # Toggle enabled off and on
    resp = await client.put(f"/api/models/{model['id']}", json={"enabled": False})
    assert resp.json()["enabled"] is False

    resp = await client.delete(f"/api/models/{model['id']}")
    assert resp.status_code == 200
    assert (await client.get("/api/models")).json()["total"] == 0


@pytest.mark.asyncio
async def test_single_default_per_provider(client):
    pid = await _make_provider(client)
    m1 = (await client.post("/api/models", json={
        "provider_id": pid, "display_name": "A", "model_id": "a"})).json()
    m2 = (await client.post("/api/models", json={
        "provider_id": pid, "display_name": "B", "model_id": "b"})).json()

    await client.put(f"/api/models/{m1['id']}", json={"is_default": True})
    await client.put(f"/api/models/{m2['id']}", json={"is_default": True})

    models = (await client.get("/api/models")).json()["models"]
    defaults = [m for m in models if m["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["id"] == m2["id"]


@pytest.mark.asyncio
async def test_model_requires_provider(client):
    resp = await client.post("/api/models", json={
        "provider_id": "nope", "display_name": "A", "model_id": "a"})
    assert resp.status_code == 400


# ── Usage ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_usage_summary_and_snapshots(client):
    resp = await client.get("/api/usage/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_tokens"] == 0
    assert data["limit"] > 0
    assert data["remaining"] == data["limit"]

    capture = await client.post("/api/usage/capture")
    assert capture.status_code == 200
    assert capture.json()["total_tokens"] == 0

    snapshots = (await client.get("/api/usage/snapshots")).json()
    assert snapshots["total"] >= 1


@pytest.mark.asyncio
async def test_usage_thresholds_update(client):
    resp = await client.put("/api/usage/thresholds", json={
        "usage_limit": 2000000, "usage_warning_threshold": 500000})
    assert resp.status_code == 200
    data = resp.json()
    assert data["usage_limit"] == 2000000
    assert data["usage_warning_threshold"] == 500000

    got = (await client.get("/api/usage/thresholds")).json()
    assert got["usage_limit"] == 2000000


@pytest.mark.asyncio
async def test_usage_thresholds_reject_invalid(client):
    resp = await client.put("/api/usage/thresholds", json={"usage_limit": 0})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_usage_warning_event(client):
    """Setting a low threshold then capturing emits a usage.warning event."""
    from app.storage.database import get_async_session
    from app.storage.repositories import ProviderRepository, CredentialRepository
    from app.core.secrets import get_secret_store

    async with get_async_session() as session:
        await ProviderRepository.create(
            session, name="P", protocol="openai-completions",
            base_url="https://x", auth_type="api-key", provider_id="p1")
        ref = get_secret_store().store("sk-test")
        cred = await CredentialRepository.create(
            session, provider_id="p1", key_masked="****", secret_ref=ref, source="manual")
        await CredentialRepository.update_fields(
            session, cred.id, state="active", usage_total=100)
        await session.commit()

    await client.put("/api/usage/thresholds", json={
        "usage_limit": 1000, "usage_warning_threshold": 10})
    await client.post("/api/usage/capture")

    resp = await client.get("/api/events", params={"event_type": "usage.warning"})
    assert resp.json()["total"] == 1
