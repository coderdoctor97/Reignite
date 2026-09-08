"""
Tests for the gateway data plane control API (first-party gateway).

The first-party gateway is served in-process under /v1 by the backend.
These tests cover:
- start/stop/restart runtime toggle (idempotent)
- status / health / config endpoints
- the stable endpoint contract
- the disabled data plane returning 503 on /v1
- stats accounting (requests, tokens, errors)
- no secrets in responses

Upstream inference is exercised separately in test_gateway_data_plane.py.
"""

import pytest

from httpx import AsyncClient, ASGITransport


@pytest.fixture(autouse=True)
def _configure_for_test(tmp_path, monkeypatch):
    """Isolated test environment."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("GCC_DATABASE_PATH", db_path)
    monkeypatch.setenv("GCC_GATEWAY_PUBLIC_BASE_URL", "http://localhost:8400")
    monkeypatch.setenv("GCC_GATEWAY_BASE_PATH", "/v1")

    from app.core.config import get_settings
    get_settings.cache_clear()

    import app.storage.database as db_mod
    db_mod._engine = None
    db_mod._session_factory = None

    import app.services.gateway_service as gw_mod
    gw_mod._service = None

    yield

    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._session_factory = None
    gw_mod._service = None


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
    from app.services.gateway_service import get_gateway_service
    get_gateway_service()._enabled_override = None
    await close_database()


# ── Toggle / lifecycle ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_gateway(client):
    """POST /api/gateway/start enables the data plane."""
    resp = await client.post("/api/gateway/start")
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["status"]["enabled"] is True
    assert data["status"]["state"] == "running"


@pytest.mark.asyncio
async def test_duplicate_start_protection(client):
    """Starting an already-started gateway is safe."""
    await client.post("/api/gateway/start")
    resp = await client.post("/api/gateway/start")
    assert resp.status_code == 200
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_stop_gateway(client):
    """POST /api/gateway/stop disables the data plane."""
    await client.post("/api/gateway/start")
    resp = await client.post("/api/gateway/stop")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert resp.json()["status"]["enabled"] is False


@pytest.mark.asyncio
async def test_duplicate_stop_safety(client):
    """Stopping twice is safe."""
    await client.post("/api/gateway/start")
    await client.post("/api/gateway/stop")
    resp = await client.post("/api/gateway/stop")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_restart_gateway(client):
    """Restart ends in the running state."""
    await client.post("/api/gateway/start")
    resp = await client.post("/api/gateway/restart")
    assert resp.status_code == 200
    assert resp.json()["status"]["enabled"] is True


@pytest.mark.asyncio
async def test_status_when_stopped(client):
    await client.post("/api/gateway/stop")
    resp = await client.get("/api/gateway/status")
    data = resp.json()
    assert data["state"] == "stopped"
    assert data["enabled"] is False


@pytest.mark.asyncio
async def test_status_when_running(client):
    await client.post("/api/gateway/start")
    resp = await client.get("/api/gateway/status")
    data = resp.json()
    assert data["state"] == "running"
    assert data["enabled"] is True
    assert data["started_at"] is not None
    assert "endpoint_url" in data


@pytest.mark.asyncio
async def test_health_when_running(client):
    await client.post("/api/gateway/start")
    resp = await client.get("/api/gateway/health")
    data = resp.json()
    assert data["healthy"] is True
    assert data["state"] == "running"


@pytest.mark.asyncio
async def test_health_when_stopped(client):
    await client.post("/api/gateway/stop")
    resp = await client.get("/api/gateway/health")
    data = resp.json()
    assert data["healthy"] is False
    assert data["state"] == "stopped"


@pytest.mark.asyncio
async def test_disabled_data_plane_returns_503(client):
    """When stopped, /v1 endpoints return 503."""
    await client.post("/api/gateway/stop")
    resp = await client.get("/v1/models")
    assert resp.status_code == 503
    resp = await client.post("/v1/chat/completions", json={"model": "auto", "messages": []})
    assert resp.status_code == 503


# ── Config / endpoint contract ───────────────────────────────────

@pytest.mark.asyncio
async def test_config_endpoint(client):
    resp = await client.get("/api/gateway/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["base_path"] == "/v1"
    assert data["endpoint_url"] == "http://localhost:8400/v1"
    assert data["public_base_url"] == "http://localhost:8400"


@pytest.mark.asyncio
async def test_endpoint_url_construction(client):
    resp = await client.get("/api/gateway/status")
    assert resp.json()["endpoint_url"] == "http://localhost:8400/v1"


@pytest.mark.asyncio
async def test_endpoint_url_stable_across_states(client):
    """The endpoint contract does not change when the gateway is stopped."""
    await client.post("/api/gateway/start")
    running_url = (await client.get("/api/gateway/status")).json()["endpoint_url"]
    await client.post("/api/gateway/stop")
    stopped_url = (await client.get("/api/gateway/status")).json()["endpoint_url"]
    assert running_url == stopped_url


@pytest.mark.asyncio
async def test_status_contains_endpoint_info(client):
    resp = await client.get("/api/gateway/status")
    data = resp.json()
    assert data["endpoint_url"].endswith("/v1")
    assert data["base_path"] == "/v1"


@pytest.mark.asyncio
async def test_action_response_shape(client):
    resp = await client.post("/api/gateway/start")
    data = resp.json()
    assert set(data.keys()) == {"success", "message", "status"}
    assert data["success"] is True
    status = data["status"]
    for key in ("state", "enabled", "started_at", "total_requests",
                "total_input_tokens", "total_output_tokens", "total_errors",
                "error_rate", "last_request_at", "last_error",
                "requests_by_model", "endpoint_url", "base_path"):
        assert key in status


@pytest.mark.asyncio
async def test_no_secrets_in_responses(client):
    """Gateway control endpoints must never contain secrets."""
    resp = await client.get("/api/gateway/status")
    text = resp.text
    assert "sk-" not in text
    resp = await client.get("/api/gateway/config")
    assert "sk-" not in resp.text
    assert "demo-key" not in resp.text


@pytest.mark.asyncio
async def test_stats_accounting(client):
    """The status endpoint reports request/token accounting."""
    from app.services.gateway_service import get_gateway_service
    service = get_gateway_service()
    service._record("demo-model", ok=True)
    service._record_tokens(10, 5)
    resp = await client.get("/api/gateway/status")
    data = resp.json()
    assert data["total_requests"] >= 1
    assert data["total_input_tokens"] >= 10
    assert data["total_output_tokens"] >= 5
    assert "demo-model" in data["requests_by_model"]
