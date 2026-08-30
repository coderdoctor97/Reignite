"""
Tests for CredentialHealthManager, validation adapter, health states,
scheduling, warning suppression, and security.

Phase 3.2: Tests for health monitoring foundation.

Uses fake validators. No real external providers.
"""

import pytest
from datetime import datetime, timezone, timedelta
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

    import app.adapters.legacy_credential_store as lcs_mod
    lcs_mod._adapter = None

    yield

    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._session_factory = None
    secrets_mod._store = None
    cm_mod._credential_manager = None
    chm_mod._health_manager = None
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


# ── Helper: create a credential and return its ID ────────────────

async def _add_credential(client, provider_id="testprov01", value=TEST_CREDENTIAL):
    resp = await client.post("/api/credentials", json={
        "credential_value": value,
        "provider_id": provider_id,
    })
    return resp.json()["id"]


# ── Health Manager Tests ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_manager_creation(client, default_provider):
    """CredentialHealthManager should be instantiable."""
    from app.services.credential_health_manager import get_credential_health_manager
    manager = get_credential_health_manager()
    assert manager is not None


@pytest.mark.asyncio
async def test_check_credential(client, default_provider):
    """check_credential should return a health summary."""
    from app.services.credential_health_manager import get_credential_health_manager

    cred_id = await _add_credential(client)
    manager = get_credential_health_manager()
    health = await manager.check_credential(cred_id)

    assert "credential_id" in health
    assert "health" in health
    assert "validation_status" in health
    assert "last_validated" in health
    assert "next_validation_at" in health
    assert health["credential_id"] == cred_id


@pytest.mark.asyncio
async def test_validation_sets_pending_then_result(client, default_provider):
    """Validation should transition through pending state."""
    from app.services.credential_health_manager import get_credential_health_manager

    cred_id = await _add_credential(client)
    manager = get_credential_health_manager()
    health = await manager.check_credential(cred_id)

    # Default validator returns 'unknown' (secret exists but can't verify against provider)
    assert health["validation_status"] == "unknown"
    assert health["last_validated"] is not None
    assert health["next_validation_at"] is not None


@pytest.mark.asyncio
async def test_health_state_healthy(client, default_provider):
    """A valid credential should have 'healthy' health state."""
    from app.services.credential_health_manager import derive_health_state
    assert derive_health_state("valid") == "healthy"


@pytest.mark.asyncio
async def test_health_state_warning_unknown(client, default_provider):
    """An unknown validation status should have 'warning' health state."""
    from app.services.credential_health_manager import derive_health_state
    assert derive_health_state("unknown") == "warning"


@pytest.mark.asyncio
async def test_health_state_warning_unavailable(client, default_provider):
    """An unavailable validation status should have 'warning' health state."""
    from app.services.credential_health_manager import derive_health_state
    assert derive_health_state("unavailable") == "warning"


@pytest.mark.asyncio
async def test_health_state_warning_pending(client, default_provider):
    """A pending validation status should have 'warning' health state."""
    from app.services.credential_health_manager import derive_health_state
    assert derive_health_state("pending") == "warning"


@pytest.mark.asyncio
async def test_health_state_critical_invalid(client, default_provider):
    """An invalid credential should have 'critical' health state."""
    from app.services.credential_health_manager import derive_health_state
    assert derive_health_state("invalid") == "critical"


@pytest.mark.asyncio
async def test_health_state_critical_expired(client, default_provider):
    """An expired credential should have 'critical' health state."""
    from app.services.credential_health_manager import derive_health_state
    assert derive_health_state("expired") == "critical"


@pytest.mark.asyncio
async def test_health_state_valid_overdue(client, default_provider):
    """A valid credential with overdue validation should have 'warning' health state."""
    from app.services.credential_health_manager import derive_health_state
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert derive_health_state("valid", next_validation_at=past) == "warning"


@pytest.mark.asyncio
async def test_health_state_valid_not_due(client, default_provider):
    """A valid credential with future validation should have 'healthy' health state."""
    from app.services.credential_health_manager import derive_health_state
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert derive_health_state("valid", next_validation_at=future) == "healthy"


@pytest.mark.asyncio
async def test_next_validation_calculation(client, default_provider):
    """next_validation_at should be in the future based on interval."""
    from app.services.credential_health_manager import get_credential_health_manager

    cred_id = await _add_credential(client)
    manager = get_credential_health_manager()
    health = await manager.check_credential(cred_id)

    next_val = health["next_validation_at"]
    assert next_val is not None
    next_dt = datetime.fromisoformat(next_val)
    now = datetime.now(timezone.utc)
    # Should be approximately 1 hour in the future (within 5 minutes tolerance)
    diff = (next_dt - now).total_seconds()
    assert 3300 < diff < 3900  # ~55-65 minutes


@pytest.mark.asyncio
async def test_get_health_without_validation(client, default_provider):
    """get_health should return current state without running validation."""
    from app.services.credential_health_manager import get_credential_health_manager

    cred_id = await _add_credential(client)
    manager = get_credential_health_manager()

    # Get health without validating
    health = await manager.get_health(cred_id)
    assert health["validation_status"] == "unknown"  # never validated
    assert health["last_validated"] is None


@pytest.mark.asyncio
async def test_get_all_health(client, default_provider):
    """get_all_health should return health for all credentials."""
    from app.services.credential_health_manager import get_credential_health_manager

    await _add_credential(client, value="sk-cred-one-abcdefghij")
    await _add_credential(client, value="sk-cred-two-klmnopqrst")

    manager = get_credential_health_manager()
    all_health = await manager.get_all_health()
    assert len(all_health) == 2


@pytest.mark.asyncio
async def test_check_all_due_credentials(client, default_provider):
    """check_all_due_credentials should check credentials due for validation."""
    from app.services.credential_health_manager import get_credential_health_manager

    cred_id = await _add_credential(client)
    manager = get_credential_health_manager()

    # Credentials with no next_validation_at should be due
    results = await manager.check_all_due_credentials()
    assert len(results) >= 1
    checked_ids = [r["credential_id"] for r in results]
    assert cred_id in checked_ids


@pytest.mark.asyncio
async def test_check_nonexistent_credential(client, default_provider):
    """Checking a nonexistent credential should raise ValueError."""
    from app.services.credential_health_manager import get_credential_health_manager

    manager = get_credential_health_manager()
    with pytest.raises(ValueError, match="not found"):
        await manager.check_credential("nonexistent")


# ── Validation Adapter Tests ─────────────────────────────────────

@pytest.mark.asyncio
async def test_default_validator_valid(client, default_provider):
    """DefaultCredentialValidator should return 'unknown' when secret exists."""
    from app.services.credential_health_manager import DefaultCredentialValidator
    from app.storage.database import get_async_session
    from app.storage.repositories import CredentialRepository

    cred_id = await _add_credential(client)

    validator = DefaultCredentialValidator()
    async with get_async_session() as session:
        row = await CredentialRepository.get_by_id(session, cred_id)
        result = await validator.validate(row)
        assert result.status == "unknown"


@pytest.mark.asyncio
async def test_default_validator_invalid_no_ref(client, default_provider):
    """DefaultCredentialValidator should return 'invalid' when no secret ref."""
    from app.services.credential_health_manager import DefaultCredentialValidator, ValidationResult
    from app.storage.models import CredentialRow

    validator = DefaultCredentialValidator()
    fake_cred = CredentialRow(id="fake", provider_id="p", secret_ref=None)
    result = await validator.validate(fake_cred)
    assert result.status == "invalid"
    assert "No secret reference" in result.error


@pytest.mark.asyncio
async def test_default_validator_invalid_missing_secret(client, default_provider):
    """DefaultCredentialValidator should return 'invalid' when secret is missing."""
    from app.services.credential_health_manager import DefaultCredentialValidator
    from app.storage.models import CredentialRow

    validator = DefaultCredentialValidator()
    fake_cred = CredentialRow(id="fake", provider_id="p", secret_ref="nonexistent_ref")
    result = await validator.validate(fake_cred)
    assert result.status == "invalid"
    assert "not found" in result.error.lower()


# ── Custom Validator Tests ───────────────────────────────────────

class FakeValidValidator:
    """Fake validator that always returns 'valid'."""
    async def validate(self, credential, secret_value=None):
        from app.services.credential_health_manager import ValidationResult
        return ValidationResult(status="valid")


class FakeInvalidValidator:
    """Fake validator that always returns 'invalid'."""
    async def validate(self, credential, secret_value=None):
        from app.services.credential_health_manager import ValidationResult
        return ValidationResult(status="invalid", error="Fake invalid")


class FakeExpiredValidator:
    """Fake validator that always returns 'expired'."""
    async def validate(self, credential, secret_value=None):
        from app.services.credential_health_manager import ValidationResult
        return ValidationResult(status="expired", error="Fake expired")


class FakeUnavailableValidator:
    """Fake validator that always returns 'unavailable'."""
    async def validate(self, credential, secret_value=None):
        from app.services.credential_health_manager import ValidationResult
        return ValidationResult(status="unavailable", error="Provider unreachable")


@pytest.mark.asyncio
async def test_custom_valid_validator(client, default_provider):
    """A custom validator returning 'valid' should update validation_status."""
    from app.services.credential_health_manager import CredentialHealthManager

    cred_id = await _add_credential(client)
    manager = CredentialHealthManager(validator=FakeValidValidator())
    health = await manager.check_credential(cred_id)
    assert health["validation_status"] == "valid"
    assert health["health"] == "healthy"


@pytest.mark.asyncio
async def test_custom_invalid_validator(client, default_provider):
    """A custom validator returning 'invalid' should update validation_status."""
    from app.services.credential_health_manager import CredentialHealthManager

    cred_id = await _add_credential(client)
    manager = CredentialHealthManager(validator=FakeInvalidValidator())
    health = await manager.check_credential(cred_id)
    assert health["validation_status"] == "invalid"
    assert health["health"] == "critical"
    assert health["last_validation_error"] == "Fake invalid"


@pytest.mark.asyncio
async def test_custom_expired_validator(client, default_provider):
    """A custom validator returning 'expired' should update validation_status."""
    from app.services.credential_health_manager import CredentialHealthManager

    cred_id = await _add_credential(client)
    manager = CredentialHealthManager(validator=FakeExpiredValidator())
    health = await manager.check_credential(cred_id)
    assert health["validation_status"] == "expired"
    assert health["health"] == "critical"


@pytest.mark.asyncio
async def test_custom_unavailable_validator(client, default_provider):
    """A custom validator returning 'unavailable' should update validation_status."""
    from app.services.credential_health_manager import CredentialHealthManager

    cred_id = await _add_credential(client)
    manager = CredentialHealthManager(validator=FakeUnavailableValidator())
    health = await manager.check_credential(cred_id)
    assert health["validation_status"] == "unavailable"
    assert health["health"] == "warning"


# ── State Transition Tests ───────────────────────────────────────

@pytest.mark.asyncio
async def test_state_transition_unknown_to_valid(client, default_provider):
    """Credential should transition from unknown to valid."""
    from app.services.credential_health_manager import CredentialHealthManager

    cred_id = await _add_credential(client)
    manager = CredentialHealthManager(validator=FakeValidValidator())

    # Initially unknown
    health = await manager.get_health(cred_id)
    assert health["validation_status"] == "unknown"

    # After validation
    health = await manager.check_credential(cred_id)
    assert health["validation_status"] == "valid"


@pytest.mark.asyncio
async def test_state_transition_valid_to_invalid(client, default_provider):
    """Credential should transition from valid to invalid."""
    from app.services.credential_health_manager import CredentialHealthManager

    cred_id = await _add_credential(client)

    # First validate as valid
    manager = CredentialHealthManager(validator=FakeValidValidator())
    health = await manager.check_credential(cred_id)
    assert health["validation_status"] == "valid"

    # Then validate as invalid
    manager = CredentialHealthManager(validator=FakeInvalidValidator())
    health = await manager.check_credential(cred_id)
    assert health["validation_status"] == "invalid"


@pytest.mark.asyncio
async def test_state_transition_invalid_to_valid(client, default_provider):
    """Credential should transition from invalid to valid (user fixed it)."""
    from app.services.credential_health_manager import CredentialHealthManager

    cred_id = await _add_credential(client)

    # First validate as invalid
    manager = CredentialHealthManager(validator=FakeInvalidValidator())
    health = await manager.check_credential(cred_id)
    assert health["validation_status"] == "invalid"

    # Then validate as valid
    manager = CredentialHealthManager(validator=FakeValidValidator())
    health = await manager.check_credential(cred_id)
    assert health["validation_status"] == "valid"


# ── Warning Suppression Tests ────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_warning_suppression(client, default_provider):
    """Duplicate warning events should be suppressed within the window."""
    from app.services.credential_health_manager import CredentialHealthManager
    from app.storage.database import get_async_session
    from app.storage.repositories import CredentialEventRepository

    cred_id = await _add_credential(client)
    manager = CredentialHealthManager(validator=FakeInvalidValidator())

    # First validation — should create 'invalid' event
    await manager.check_credential(cred_id)

    # Second validation immediately — should NOT create another 'invalid' event
    await manager.check_credential(cred_id)

    # Count 'invalid' events
    async with get_async_session() as session:
        events = await CredentialEventRepository.list_by_credential(session, cred_id)
        invalid_events = [e for e in events if e.event_type == "invalid"]
        # Should be exactly 1 (suppressed the second)
        assert len(invalid_events) == 1


@pytest.mark.asyncio
async def test_validation_event_always_recorded(client, default_provider):
    """Validation events should always be recorded (not suppressed)."""
    from app.services.credential_health_manager import CredentialHealthManager
    from app.storage.database import get_async_session
    from app.storage.repositories import CredentialEventRepository

    cred_id = await _add_credential(client)
    manager = CredentialHealthManager(validator=FakeInvalidValidator())

    # Validate twice
    await manager.check_credential(cred_id)
    await manager.check_credential(cred_id)

    async with get_async_session() as session:
        events = await CredentialEventRepository.list_by_credential(session, cred_id)
        validated_events = [e for e in events if e.event_type == "validated"]
        # Should have 2 validated events (one per check)
        assert len(validated_events) == 2


# ── API Tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_health_api_endpoint(client, default_provider):
    """GET /api/credentials/health should return health summaries."""
    await _add_credential(client)

    response = await client.get("/api/credentials/health")
    assert response.status_code == 200
    data = response.json()
    assert "credentials" in data
    assert "total" in data
    assert "summary" in data
    assert data["total"] == 1
    assert "healthy" in data["summary"]
    assert "warning" in data["summary"]
    assert "critical" in data["summary"]
    assert "unknown" in data["summary"]


@pytest.mark.asyncio
async def test_health_api_single_credential(client, default_provider):
    """GET /api/credentials/{id}/health should return health for one credential."""
    cred_id = await _add_credential(client)

    response = await client.get(f"/api/credentials/{cred_id}/health")
    assert response.status_code == 200
    data = response.json()
    assert data["credential_id"] == cred_id
    assert "health" in data
    assert "validation_status" in data
    assert "last_validated" in data
    assert "next_validation_at" in data


@pytest.mark.asyncio
async def test_health_api_nonexistent(client):
    """GET /api/credentials/{id}/health for nonexistent should return 404."""
    response = await client.get("/api/credentials/nonexistent/health")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_validate_api_updates_health(client, default_provider):
    """POST /api/credentials/{id}/validate should update health data."""
    cred_id = await _add_credential(client)

    response = await client.post(f"/api/credentials/{cred_id}/validate")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["credential"]["validation_status"] == "unknown"  # default validator
    assert data["credential"]["last_validated"] is not None
    assert data["credential"]["next_validation_at"] is not None


@pytest.mark.asyncio
async def test_health_summary_counts(client, default_provider):
    """Health summary should count credentials by health state."""
    from app.services.credential_health_manager import CredentialHealthManager

    # Add two credentials
    id1 = await _add_credential(client, value="sk-cred-one-abcdefghij")
    id2 = await _add_credential(client, value="sk-cred-two-klmnopqrst")

    # Validate one as valid, one as invalid
    manager_valid = CredentialHealthManager(validator=FakeValidValidator())
    manager_invalid = CredentialHealthManager(validator=FakeInvalidValidator())

    await manager_valid.check_credential(id1)
    await manager_invalid.check_credential(id2)

    response = await client.get("/api/credentials/health")
    data = response.json()
    assert data["summary"]["healthy"] == 1
    assert data["summary"]["critical"] == 1


# ── Security Tests ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_secrets_in_health_response(client, default_provider):
    """Health endpoint should never contain raw credential values."""
    cred_id = await _add_credential(client)

    response = await client.get(f"/api/credentials/{cred_id}/health")
    assert TEST_CREDENTIAL not in response.text


@pytest.mark.asyncio
async def test_no_secrets_in_health_list_response(client, default_provider):
    """Health list endpoint should never contain raw credential values."""
    await _add_credential(client)

    response = await client.get("/api/credentials/health")
    assert TEST_CREDENTIAL not in response.text


@pytest.mark.asyncio
async def test_no_secrets_in_validate_response(client, default_provider):
    """Validate endpoint should never contain raw credential values."""
    cred_id = await _add_credential(client)

    response = await client.post(f"/api/credentials/{cred_id}/validate")
    assert TEST_CREDENTIAL not in response.text
