"""
Tests for CredentialMonitor — background monitoring service.

Phase 3.3: Tests for monitor lifecycle, scheduling, non-overlapping cycles,
health change events, and API endpoints.

Uses fake validators. No real external providers.
"""

import pytest
import asyncio
from pathlib import Path

from httpx import AsyncClient, ASGITransport


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _configure_for_test(tmp_path, monkeypatch):
    """Set up isolated test environment."""
    db_path = str(tmp_path / "test.db")
    legacy_dir = str(tmp_path / "legacy")

    monkeypatch.setenv("GCC_DATABASE_PATH", db_path)
    monkeypatch.setenv("GCC_LEGACY_BASE_DIR", legacy_dir)
    monkeypatch.setenv("GCC_GATEWAY_PORT", "15800")
    monkeypatch.setenv("GCC_CREDENTIAL_VALIDATION_INTERVAL", "3600")
    monkeypatch.setenv("GCC_CREDENTIAL_MONITOR_ENABLED", "true")
    monkeypatch.setenv("GCC_CREDENTIAL_MONITOR_INTERVAL", "60")

    from app.core.config import get_settings
    get_settings.cache_clear()

    import app.storage.database as db_mod
    db_mod._engine = None
    db_mod._session_factory = None

    import app.core.secrets as secrets_mod
    secrets_mod._store = None

    import app.services.credential_manager as cm_mod
    cm_mod._credential_manager = None

    import app.services.credential_health_manager as chm_mod
    chm_mod._health_manager = None

    import app.services.credential_monitor as monitor_mod
    monitor_mod._monitor = None

    import app.adapters.legacy_credential_store as lcs_mod
    lcs_mod._adapter = None

    yield

    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._session_factory = None
    secrets_mod._store = None
    cm_mod._credential_manager = None
    chm_mod._health_manager = None
    monitor_mod._monitor = None
    lcs_mod._adapter = None


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
    # Stop monitor if running
    from app.services.credential_monitor import get_credential_monitor
    monitor = get_credential_monitor()
    await monitor.stop()
    await close_database()


@pytest.fixture
async def default_provider(client):
    """Create a default provider for testing."""
    from app.storage.database import get_async_session
    from app.storage.repositories import ProviderRepository
    async with get_async_session() as session:
        provider = await ProviderRepository.create(
            session,
            name="Test Provider",
            protocol="openai-completions",
            base_url="https://api.example.com",
            auth_type="api-key",
            provider_id="testprov01",
        )
        await session.commit()
        return provider


TEST_CREDENTIAL = "sk-test-abcdefghijklmnop1234"


async def _add_credential(client, provider_id="testprov01", value=TEST_CREDENTIAL):
    resp = await client.post("/api/credentials", json={
        "credential_value": value,
        "provider_id": provider_id,
    })
    return resp.json()["id"]


# ── Monitor Lifecycle Tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_monitor_creation(client):
    """CredentialMonitor should be instantiable."""
    from app.services.credential_monitor import get_credential_monitor
    monitor = get_credential_monitor()
    assert monitor is not None


@pytest.mark.asyncio
async def test_monitor_start(client):
    """Starting the monitor should set it as running."""
    from app.services.credential_monitor import get_credential_monitor
    monitor = get_credential_monitor()
    await monitor.start()
    assert monitor.is_running()
    await monitor.stop()


@pytest.mark.asyncio
async def test_monitor_stop(client):
    """Stopping the monitor should set it as not running."""
    from app.services.credential_monitor import get_credential_monitor
    monitor = get_credential_monitor()
    await monitor.start()
    assert monitor.is_running()
    await monitor.stop()
    assert not monitor.is_running()


@pytest.mark.asyncio
async def test_monitor_idempotent_start(client):
    """Starting the monitor twice should not create duplicate tasks."""
    from app.services.credential_monitor import get_credential_monitor
    monitor = get_credential_monitor()
    await monitor.start()
    task1 = monitor._task
    await monitor.start()  # Should be idempotent
    task2 = monitor._task
    assert task1 is task2
    await monitor.stop()


@pytest.mark.asyncio
async def test_monitor_disabled(tmp_path, monkeypatch):
    """Monitor should not start when disabled in config."""
    monkeypatch.setenv("GCC_CREDENTIAL_MONITOR_ENABLED", "false")
    from app.core.config import get_settings
    get_settings.cache_clear()

    from app.services.credential_monitor import CredentialMonitor
    monitor = CredentialMonitor()
    await monitor.start()
    assert not monitor.is_running()
    await monitor.stop()


@pytest.mark.asyncio
async def test_monitor_status(client):
    """Monitor status should return correct fields."""
    from app.services.credential_monitor import get_credential_monitor
    monitor = get_credential_monitor()
    status = monitor.status()
    assert hasattr(status, "enabled")
    assert hasattr(status, "running")
    assert hasattr(status, "interval_seconds")
    assert hasattr(status, "last_run")
    assert hasattr(status, "credentials_checked")
    assert hasattr(status, "total_cycles")


@pytest.mark.asyncio
async def test_monitor_status_api(client):
    """GET /api/monitor/status should return monitor status."""
    response = await client.get("/api/monitor/status")
    assert response.status_code == 200
    data = response.json()
    assert "enabled" in data
    assert "running" in data
    assert "interval_seconds" in data
    assert "last_run" in data
    assert "credentials_checked" in data
    assert "total_cycles" in data


# ── Run Once Tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_monitor_run_once(client, default_provider):
    """run_once should execute a single monitoring cycle."""
    from app.services.credential_monitor import get_credential_monitor

    await _add_credential(client)

    monitor = get_credential_monitor()
    result = await monitor.run_once()

    assert result["success"] is True
    assert result["credentials_checked"] >= 1
    assert result["cycle_number"] == 1


@pytest.mark.asyncio
async def test_monitor_run_once_api(client, default_provider):
    """POST /api/monitor/run should trigger a monitoring cycle."""
    await _add_credential(client)

    response = await client.post("/api/monitor/run")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["credentials_checked"] >= 1


@pytest.mark.asyncio
async def test_monitor_run_once_updates_status(client, default_provider):
    """run_once should update monitor statistics."""
    from app.services.credential_monitor import get_credential_monitor

    await _add_credential(client)

    monitor = get_credential_monitor()
    await monitor.run_once()

    status = monitor.status()
    assert status.total_cycles == 1
    assert status.credentials_checked >= 1
    assert status.last_run is not None
    assert status.last_success is not None


# ── Non-overlapping Cycle Tests ──────────────────────────────────

@pytest.mark.asyncio
async def test_monitor_no_overlapping_cycles(client, default_provider):
    """Concurrent run_once calls should not overlap."""
    from app.services.credential_monitor import get_credential_monitor

    await _add_credential(client)

    monitor = get_credential_monitor()

    # Start a cycle (don't await)
    task1 = asyncio.create_task(monitor.run_once())
    # Give it a moment to acquire the lock
    await asyncio.sleep(0.01)

    # Try to run another cycle — should be rejected
    result2 = await monitor.run_once()
    assert result2["cycle_in_progress"] is True

    # Wait for the first to complete
    result1 = await task1
    assert result1["success"] is True


# ── Due Credential Tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_monitor_checks_due_credentials(client, default_provider):
    """Monitor should check credentials that are due for validation."""
    from app.services.credential_monitor import get_credential_monitor

    cred_id = await _add_credential(client)

    monitor = get_credential_monitor()
    result = await monitor.run_once()

    assert result["credentials_checked"] >= 1
    assert result["checks_succeeded"] >= 1


@pytest.mark.asyncio
async def test_monitor_survives_individual_failure(client, default_provider):
    """One credential check failure should not stop the monitor."""
    from app.services.credential_monitor import CredentialMonitor
    from app.services.credential_health_manager import CredentialHealthManager

    # Create a health manager that fails on one credential
    class FailingHealthManager:
        async def check_all_due_credentials(self):
            raise Exception("Simulated failure")
        async def get_all_health(self):
            return []
        async def get_health(self, cred_id):
            return {"credential_id": cred_id, "health": "unknown"}

    monitor = CredentialMonitor(health_manager=FailingHealthManager())
    result = await monitor.run_once()

    # The cycle should fail but the monitor should still be alive
    assert result["success"] is False
    assert "failed" in result["message"].lower()


# ── Health Change Event Tests ────────────────────────────────────

@pytest.mark.asyncio
async def test_monitor_emits_events(client, default_provider):
    """Monitor should emit events for monitoring cycles."""
    from app.storage.database import get_async_session
    from app.storage.repositories import EventRepository

    await _add_credential(client)

    from app.services.credential_monitor import get_credential_monitor
    monitor = get_credential_monitor()
    await monitor.run_once()

    async with get_async_session() as session:
        events = await EventRepository.list_by_type(session, "monitor.cycle_completed", limit=5)
        assert len(events) >= 1


@pytest.mark.asyncio
async def test_monitor_health_change_detection(client, default_provider):
    """Monitor should detect health state changes between cycles."""
    from app.services.credential_monitor import get_credential_monitor

    cred_id = await _add_credential(client)

    # First cycle: credential is 'unknown' (warning)
    monitor = get_credential_monitor()
    await monitor.run_once()

    # Verify the previous state was tracked
    assert cred_id in monitor._previous_health_states


# ── Clean Shutdown Tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_monitor_clean_shutdown(client):
    """Monitor should stop cleanly without dangling tasks."""
    from app.services.credential_monitor import get_credential_monitor

    monitor = get_credential_monitor()
    await monitor.start()
    assert monitor.is_running()

    await monitor.stop()
    assert not monitor.is_running()
    assert monitor._task is None or monitor._task.done()


@pytest.mark.asyncio
async def test_monitor_stop_when_not_running(client):
    """Stopping a non-running monitor should be safe."""
    from app.services.credential_monitor import get_credential_monitor

    monitor = get_credential_monitor()
    # Don't start — just stop
    await monitor.stop()
    assert not monitor.is_running()


# ── Security Tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_secrets_in_monitor_status(client, default_provider):
    """Monitor status should never contain credential secrets."""
    await _add_credential(client)

    response = await client.get("/api/monitor/status")
    assert TEST_CREDENTIAL not in response.text


@pytest.mark.asyncio
async def test_no_secrets_in_monitor_run(client, default_provider):
    """Monitor run response should never contain credential secrets."""
    await _add_credential(client)

    response = await client.post("/api/monitor/run")
    assert TEST_CREDENTIAL not in response.text
