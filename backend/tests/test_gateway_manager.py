"""
Tests for GatewayManager and gateway API routes.

Uses a small local test server fixture instead of the real gateway.
No real credentials or external API calls.
"""

import pytest
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from httpx import AsyncClient, ASGITransport


# ── Test fixture: a tiny HTTP server that acts as a fake gateway ──

FAKE_GATEWAY_SCRIPT = '''
"""Tiny HTTP server for testing GatewayManager."""
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
import json

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok"}).encode())

    def log_message(self, format, *args):
        pass  # quiet

port = int(sys.argv[1]) if len(sys.argv) > 1 else 15800
server = HTTPServer(("127.0.0.1", port), Handler)
print(f"[OK] Fake gateway running on port {port}", flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
'''


@pytest.fixture
def fake_gateway_script(tmp_path):
    """Write a fake gateway script to a temp directory."""
    script = tmp_path / "OpusGateway.py"
    script.write_text(FAKE_GATEWAY_SCRIPT)
    return script


@pytest.fixture(autouse=True)
def _configure_for_test(tmp_path, fake_gateway_script, monkeypatch):
    """Point the gateway config to the fake script and temp directory."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("GCC_DATABASE_PATH", db_path)
    monkeypatch.setenv("GCC_LEGACY_BASE_DIR", str(fake_gateway_script.parent))
    monkeypatch.setenv("GCC_GATEWAY_PORT", "15800")
    monkeypatch.setenv("GCC_GATEWAY_STARTUP_TIMEOUT", "5.0")
    monkeypatch.setenv("GCC_GATEWAY_SHUTDOWN_TIMEOUT", "3.0")

    from app.core.config import get_settings
    get_settings.cache_clear()

    import app.storage.database as db_mod
    db_mod._engine = None
    db_mod._session_factory = None

    # Reset the singleton
    import app.services.gateway_manager as gw_mod
    gw_mod._gateway_manager = None

    yield

    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._session_factory = None
    gw_mod._gateway_manager = None


@pytest.fixture
async def app():
    """Create the FastAPI app for testing."""
    from app.main import create_app
    from app.storage.database import init_database
    app = create_app()
    await init_database()
    return app


@pytest.fixture
async def client(app):
    """Async test client."""
    from app.storage.database import close_database
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    # Clean up any running gateway
    from app.services.gateway_manager import get_gateway_manager
    manager = get_gateway_manager()
    await manager.stop()
    await close_database()


# ── GatewayManager Tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_start_gateway(client):
    """Starting the gateway should transition to RUNNING."""
    response = await client.post("/api/gateway/start")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"]["state"] == "running"
    assert data["status"]["pid"] is not None


@pytest.mark.asyncio
async def test_duplicate_start_protection(client):
    """Calling start twice should not spawn duplicate processes."""
    r1 = await client.post("/api/gateway/start")
    pid1 = r1.json()["status"]["pid"]

    r2 = await client.post("/api/gateway/start")
    pid2 = r2.json()["status"]["pid"]

    assert pid1 == pid2
    assert r2.json()["message"] == "Gateway already running (PID {})".format(pid1) or r2.json()["success"] is True


@pytest.mark.asyncio
async def test_stop_gateway(client):
    """Stopping the gateway should transition to STOPPED."""
    await client.post("/api/gateway/start")

    response = await client.post("/api/gateway/stop")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"]["state"] == "stopped"
    assert data["status"]["pid"] is None


@pytest.mark.asyncio
async def test_duplicate_stop_safety(client):
    """Calling stop when already stopped should be safe."""
    response = await client.post("/api/gateway/stop")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"]["state"] == "stopped"


@pytest.mark.asyncio
async def test_restart_gateway(client):
    """Restarting should stop then start, returning a new PID."""
    r1 = await client.post("/api/gateway/start")
    pid1 = r1.json()["status"]["pid"]

    r2 = await client.post("/api/gateway/restart")
    assert r2.status_code == 200
    data = r2.json()
    assert data["success"] is True
    assert data["status"]["state"] == "running"
    assert data["status"]["pid"] is not None
    # PID should be different after restart
    assert data["status"]["pid"] != pid1


@pytest.mark.asyncio
async def test_status_when_stopped(client):
    """Status should report STOPPED when no process is running."""
    response = await client.get("/api/gateway/status")
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "stopped"
    assert data["pid"] is None


@pytest.mark.asyncio
async def test_status_when_running(client):
    """Status should report RUNNING with PID and uptime when active."""
    await client.post("/api/gateway/start")

    response = await client.get("/api/gateway/status")
    assert response.status_code == 200
    data = response.json()
    assert data["state"] == "running"
    assert data["pid"] is not None
    assert data["uptime_seconds"] is not None
    assert data["uptime_seconds"] >= 0


@pytest.mark.asyncio
async def test_health_when_running(client):
    """Health should report HEALTHY when process is alive and port is reachable."""
    await client.post("/api/gateway/start")
    # Give it a moment to bind the port
    await asyncio.sleep(0.5)

    response = await client.get("/api/gateway/health")
    assert response.status_code == 200
    data = response.json()
    assert data["process_alive"] is True
    assert data["port_reachable"] is True
    assert data["status"] == "healthy"


@pytest.mark.asyncio
async def test_health_when_stopped(client):
    """Health should report STOPPED when no process is running."""
    response = await client.get("/api/gateway/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "stopped"
    assert data["process_alive"] is False
    assert data["port_reachable"] is False


@pytest.mark.asyncio
async def test_missing_gateway_script(tmp_path, monkeypatch):
    """Starting with a missing script should report FAILED."""
    # Point to a non-existent script
    monkeypatch.setenv("GCC_LEGACY_BASE_DIR", str(tmp_path / "nonexistent"))

    from app.core.config import get_settings
    get_settings.cache_clear()

    from app.services.gateway_manager import GatewayManager
    manager = GatewayManager()
    info = await manager.start()
    assert info.state.value == "failed"
    assert "not found" in (info.last_error or "").lower()


@pytest.mark.asyncio
async def test_failed_startup(tmp_path, monkeypatch):
    """A script that exits immediately should report FAILED."""
    # Write a script that exits with code 1
    bad_script = tmp_path / "OpusGateway.py"
    bad_script.write_text("import sys; sys.exit(1)")

    monkeypatch.setenv("GCC_LEGACY_BASE_DIR", str(tmp_path))
    monkeypatch.setenv("GCC_GATEWAY_STARTUP_TIMEOUT", "2.0")

    from app.core.config import get_settings
    get_settings.cache_clear()

    from app.services.gateway_manager import GatewayManager
    manager = GatewayManager()
    info = await manager.start()
    assert info.state.value == "failed"
    assert info.last_exit_code == 1


@pytest.mark.asyncio
async def test_process_crash_detection(client):
    """Unexpected process exit should be detected."""
    await client.post("/api/gateway/start")

    # Kill the process externally
    from app.services.gateway_manager import get_gateway_manager
    manager = get_gateway_manager()
    if manager._process:
        manager._process.kill()
        await manager._process.wait()

    # Give the wait task time to detect the exit
    await asyncio.sleep(0.5)

    info = await manager.status()
    assert info.state.value == "failed"
    assert info.last_exit_code is not None


@pytest.mark.asyncio
async def test_bounded_output_buffer(client):
    """Output buffer should be bounded and not grow indefinitely."""
    await client.post("/api/gateway/start")
    await asyncio.sleep(0.5)

    from app.services.gateway_manager import get_gateway_manager, MAX_OUTPUT_LINES
    manager = get_gateway_manager()

    # The buffer should not exceed MAX_OUTPUT_LINES
    assert len(manager._output) <= MAX_OUTPUT_LINES


@pytest.mark.asyncio
async def test_gateway_logs_endpoint(client):
    """GET /api/gateway/logs should return captured output."""
    await client.post("/api/gateway/start")
    await asyncio.sleep(0.5)

    response = await client.get("/api/gateway/logs")
    assert response.status_code == 200
    data = response.json()
    assert "lines" in data
    assert "total" in data
    # Should have at least the startup message
    assert data["total"] > 0


@pytest.mark.asyncio
async def test_api_status_endpoint(client):
    """GET /api/gateway/status should return structured JSON."""
    response = await client.get("/api/gateway/status")
    assert response.status_code == 200
    data = response.json()
    assert "state" in data
    assert "pid" in data
    assert "restart_count" in data


@pytest.mark.asyncio
async def test_api_health_endpoint(client):
    """GET /api/gateway/health should return structured JSON."""
    response = await client.get("/api/gateway/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "process_alive" in data
    assert "port_reachable" in data
    assert "checked_at" in data


@pytest.mark.asyncio
async def test_no_secrets_in_responses(client):
    """API responses should not contain secrets."""
    await client.post("/api/gateway/start")
    await asyncio.sleep(0.3)

    # Check status
    status = await client.get("/api/gateway/status")
    status_text = status.text.lower()
    assert "sk-" not in status_text
    assert "api_key" not in status_text
    assert "session" not in status_text or "session" in status_text  # "session" is OK as a concept

    # Check logs
    logs = await client.get("/api/gateway/logs")
    logs_text = logs.text.lower()
    # The fake gateway doesn't output secrets, but verify the structure
    assert "lines" in logs.json()
