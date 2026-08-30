"""
Tests for GatewayManager and gateway API routes.

Phase 2.2: Tests for config endpoint, endpoint URL construction, HTTP health
check, and the new response shapes.

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
        if self.path == "/":
            # Mimic the legacy gateway root response
            body = b"Opus Gateway API Proxy - nothing here (use /v1...)"
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Server", "OpusGateway")
            self.end_headers()
            self.wfile.write(body)
            return
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
    monkeypatch.setenv("GCC_GATEWAY_PROTOCOL", "http")
    monkeypatch.setenv("GCC_GATEWAY_BASE_PATH", "/v1")
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
    assert data["status"]["process"]["state"] == "running"
    assert data["status"]["process"]["pid"] is not None


@pytest.mark.asyncio
async def test_duplicate_start_protection(client):
    """Calling start twice should not spawn duplicate processes."""
    r1 = await client.post("/api/gateway/start")
    pid1 = r1.json()["status"]["process"]["pid"]

    r2 = await client.post("/api/gateway/start")
    pid2 = r2.json()["status"]["process"]["pid"]

    assert pid1 == pid2
    assert r2.json()["success"] is True


@pytest.mark.asyncio
async def test_stop_gateway(client):
    """Stopping the gateway should transition to STOPPED."""
    await client.post("/api/gateway/start")

    response = await client.post("/api/gateway/stop")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"]["process"]["state"] == "stopped"
    assert data["status"]["process"]["pid"] is None


@pytest.mark.asyncio
async def test_duplicate_stop_safety(client):
    """Calling stop when already stopped should be safe."""
    response = await client.post("/api/gateway/stop")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["status"]["process"]["state"] == "stopped"


@pytest.mark.asyncio
async def test_restart_gateway(client):
    """Restarting should stop then start, returning a new PID."""
    r1 = await client.post("/api/gateway/start")
    pid1 = r1.json()["status"]["process"]["pid"]

    r2 = await client.post("/api/gateway/restart")
    assert r2.status_code == 200
    data = r2.json()
    assert data["success"] is True
    assert data["status"]["process"]["state"] == "running"
    assert data["status"]["process"]["pid"] is not None
    # PID should be different after restart
    assert data["status"]["process"]["pid"] != pid1


@pytest.mark.asyncio
async def test_status_when_stopped(client):
    """Status should report STOPPED when no process is running."""
    response = await client.get("/api/gateway/status")
    assert response.status_code == 200
    data = response.json()
    assert data["process"]["state"] == "stopped"
    assert data["process"]["pid"] is None


@pytest.mark.asyncio
async def test_status_when_running(client):
    """Status should report RUNNING with PID and uptime when active."""
    await client.post("/api/gateway/start")

    response = await client.get("/api/gateway/status")
    assert response.status_code == 200
    data = response.json()
    assert data["process"]["state"] == "running"
    assert data["process"]["pid"] is not None
    assert data["process"]["uptime_seconds"] is not None
    assert data["process"]["uptime_seconds"] >= 0


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
    assert data["http_responsive"] is True
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
    assert data["http_responsive"] is False


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
    """GET /api/gateway/status should return structured JSON with process and endpoint."""
    response = await client.get("/api/gateway/status")
    assert response.status_code == 200
    data = response.json()
    assert "process" in data
    assert "endpoint" in data
    assert "state" in data["process"]
    assert "pid" in data["process"]
    assert "restart_count" in data["process"]


@pytest.mark.asyncio
async def test_api_health_endpoint(client):
    """GET /api/gateway/health should return structured JSON."""
    response = await client.get("/api/gateway/health")
    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "process_alive" in data
    assert "port_reachable" in data
    assert "http_responsive" in data
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

    # Check logs
    logs = await client.get("/api/gateway/logs")
    logs_text = logs.text.lower()
    # The fake gateway doesn't output secrets, but verify the structure
    assert "lines" in logs.json()


# ── Phase 2.2: Configuration and Endpoint Tests ──────────────────

@pytest.mark.asyncio
async def test_config_endpoint(client):
    """GET /api/gateway/config should return the gateway configuration."""
    response = await client.get("/api/gateway/config")
    assert response.status_code == 200
    data = response.json()
    assert data["host"] == "127.0.0.1"
    assert data["port"] == 15800
    assert data["protocol"] == "http"
    assert data["base_path"] == "/v1"
    assert data["endpoint_url"] == "http://127.0.0.1:15800/v1"
    assert data["base_url"] == "http://127.0.0.1:15800"
    assert data["script"] == "OpusGateway.py"
    assert "startup_timeout" in data
    assert "shutdown_timeout" in data


@pytest.mark.asyncio
async def test_endpoint_url_construction(client):
    """Endpoint URL should be constructed from protocol, host, port, and base_path."""
    response = await client.get("/api/gateway/config")
    data = response.json()
    expected = f"{data['protocol']}://{data['host']}:{data['port']}{data['base_path']}"
    assert data["endpoint_url"] == expected


@pytest.mark.asyncio
async def test_status_contains_endpoint_info(client):
    """Status response should include endpoint information."""
    response = await client.get("/api/gateway/status")
    data = response.json()
    assert "endpoint" in data
    endpoint = data["endpoint"]
    assert "host" in endpoint
    assert "port" in endpoint
    assert "protocol" in endpoint
    assert "base_path" in endpoint
    assert "url" in endpoint
    assert "base_url" in endpoint
    assert endpoint["url"] == "http://127.0.0.1:15800/v1"


@pytest.mark.asyncio
async def test_status_process_info(client):
    """Status response should have process info in a nested object."""
    response = await client.get("/api/gateway/status")
    data = response.json()
    assert "process" in data
    process = data["process"]
    assert "state" in process
    assert "pid" in process
    assert "uptime_seconds" in process
    assert "restart_count" in process
    assert "last_exit_code" in process
    assert "last_error" in process
    assert "start_time" in process
    assert "stop_time" in process


@pytest.mark.asyncio
async def test_action_response_shape(client):
    """Action responses should include success, message, and nested status."""
    response = await client.post("/api/gateway/start")
    data = response.json()
    assert "success" in data
    assert "message" in data
    assert "status" in data
    assert "process" in data["status"]
    assert "endpoint" in data["status"]


@pytest.mark.asyncio
async def test_http_health_check(client):
    """HTTP health check should detect the fake gateway's HTTP response."""
    await client.post("/api/gateway/start")
    await asyncio.sleep(0.5)

    from app.services.gateway_manager import get_gateway_manager
    manager = get_gateway_manager()

    # The fake gateway responds to GET / with 404 — that's still HTTP responsive
    http_ok = await manager._check_http()
    assert http_ok is True


@pytest.mark.asyncio
async def test_http_health_check_when_stopped(client):
    """HTTP health check should return False when gateway is not running."""
    from app.services.gateway_manager import get_gateway_manager
    manager = get_gateway_manager()

    http_ok = await manager._check_http()
    assert http_ok is False


@pytest.mark.asyncio
async def test_get_config_method(client):
    """GatewayManager.get_config() should return a dict with all config fields."""
    from app.services.gateway_manager import get_gateway_manager
    manager = get_gateway_manager()
    config = manager.get_config()

    assert isinstance(config, dict)
    assert "host" in config
    assert "port" in config
    assert "protocol" in config
    assert "base_path" in config
    assert "endpoint_url" in config
    assert "base_url" in config
    assert "script" in config
    assert "working_directory" in config
    assert "startup_timeout" in config
    assert "shutdown_timeout" in config


@pytest.mark.asyncio
async def test_endpoint_url_stable_across_states(client):
    """Endpoint URL should be the same regardless of gateway state."""
    # When stopped
    r1 = await client.get("/api/gateway/status")
    url1 = r1.json()["endpoint"]["url"]

    # When running
    await client.post("/api/gateway/start")
    r2 = await client.get("/api/gateway/status")
    url2 = r2.json()["endpoint"]["url"]

    assert url1 == url2
    assert url1 == "http://127.0.0.1:15800/v1"


@pytest.mark.asyncio
async def test_config_endpoint_independent_of_process(client):
    """Config endpoint should work regardless of gateway state."""
    # When stopped
    r1 = await client.get("/api/gateway/config")
    assert r1.status_code == 200
    assert r1.json()["endpoint_url"] == "http://127.0.0.1:15800/v1"

    # When running
    await client.post("/api/gateway/start")
    r2 = await client.get("/api/gateway/config")
    assert r2.status_code == 200
    assert r2.json()["endpoint_url"] == "http://127.0.0.1:15800/v1"


@pytest.mark.asyncio
async def test_health_includes_http_responsive_field(client):
    """Health response should include http_responsive field."""
    response = await client.get("/api/gateway/health")
    data = response.json()
    assert "http_responsive" in data
    assert data["http_responsive"] is False  # not running


@pytest.mark.asyncio
async def test_health_http_responsive_when_running(client):
    """Health should report http_responsive=True when gateway is serving HTTP."""
    await client.post("/api/gateway/start")
    await asyncio.sleep(0.5)

    response = await client.get("/api/gateway/health")
    data = response.json()
    assert data["http_responsive"] is True
    assert data["status"] == "healthy"
