"""
Tests for the full data model — repositories, secret store, and migrations.

All tests use isolated temporary databases. No real secrets are used.
"""

import pytest
import os
import json
from pathlib import Path
from sqlalchemy import text

from app.core.secrets import FileSecretStore, set_secret_store


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Point the database to a temporary directory for each test."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("GCC_DATABASE_PATH", db_path)
    # Clear cached settings so they pick up the new env var
    from app.core.config import get_settings
    get_settings.cache_clear()
    # Reset the database module's engine so it reconnects
    import app.storage.database as db_mod
    db_mod._engine = None
    db_mod._session_factory = None
    yield
    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._session_factory = None


@pytest.fixture
async def session():
    """Provide a clean database session for each test."""
    from app.storage.database import init_database, get_async_session, close_database
    await init_database()
    async with get_async_session() as s:
        yield s
    await close_database()


@pytest.fixture
def secret_store(tmp_path):
    """Provide an isolated FileSecretStore."""
    store = FileSecretStore(base_dir=str(tmp_path / "secrets"))
    set_secret_store(store)
    yield store
    set_secret_store(None)  # type: ignore


# ── Database / Migration Tests ──────────────────────────────────

@pytest.mark.asyncio
async def test_fresh_database_creation():
    """init_database should create all 9 tables on a fresh database."""
    from app.storage.database import init_database, get_async_session, close_database
    await init_database()

    async with get_async_session() as session:
        result = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        )
        tables = [row[0] for row in result.fetchall()]

    expected = [
        "credentials", "credential_events", "events", "health_checks", "models",
        "providers", "sessions", "settings", "usage_snapshots",
    ]
    for t in expected:
        assert t in tables, f"Missing table: {t}"

    await close_database()


@pytest.mark.asyncio
async def test_migration_upgrade():
    """Alembic should be able to upgrade a fresh database."""
    from alembic.config import Config
    from alembic.command import upgrade, current
    from app.core.config import get_settings

    settings = get_settings()
    alembic_cfg = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{settings.database_path}")

    # Run upgrade
    upgrade(alembic_cfg, "head")

    # Verify version is set
    # (just checking it doesn't raise)
    current(alembic_cfg)


# ── Provider Repository Tests ───────────────────────────────────

@pytest.mark.asyncio
async def test_provider_crud(session):
    """Create, read, update, delete a provider."""
    from app.storage.repositories import ProviderRepository

    # Create
    provider = await ProviderRepository.create(
        session,
        name="Test Provider",
        protocol="openai-completions",
        base_url="https://api.example.com",
    )
    assert provider.id is not None
    assert provider.name == "Test Provider"
    assert provider.enabled is True

    # Read
    fetched = await ProviderRepository.get_by_id(session, provider.id)
    assert fetched is not None
    assert fetched.name == "Test Provider"

    # List
    all_providers = await ProviderRepository.list_all(session)
    assert len(all_providers) == 1

    # Update
    updated = await ProviderRepository.update_fields(
        session, provider.id, name="Updated Provider"
    )
    assert updated is True
    fetched = await ProviderRepository.get_by_id(session, provider.id)
    assert fetched.name == "Updated Provider"

    # Delete
    deleted = await ProviderRepository.delete_by_id(session, provider.id)
    assert deleted is True
    fetched = await ProviderRepository.get_by_id(session, provider.id)
    assert fetched is None


# ── Model Repository Tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_model_provider_relationship(session):
    """Models should be linked to providers and cascade on delete."""
    from app.storage.repositories import ProviderRepository, ModelRepository

    # Create provider
    provider = await ProviderRepository.create(
        session,
        name="Provider A",
        protocol="openai-completions",
        base_url="https://api.example.com",
    )

    # Create models
    model1 = await ModelRepository.create(
        session,
        provider_id=provider.id,
        display_name="GPT-4",
        model_id="gpt-4",
        context_window=128000,
    )
    model2 = await ModelRepository.create(
        session,
        provider_id=provider.id,
        display_name="GPT-3.5",
        model_id="gpt-3.5-turbo",
        context_window=16385,
    )

    # List by provider
    models = await ModelRepository.list_by_provider(session, provider.id)
    assert len(models) == 2

    # Update
    await ModelRepository.update_fields(session, model1.id, is_default=True)
    fetched = await ModelRepository.get_by_id(session, model1.id)
    assert fetched.is_default is True

    # Delete provider should cascade to models
    await ProviderRepository.delete_by_id(session, provider.id)
    models = await ModelRepository.list_by_provider(session, provider.id)
    assert len(models) == 0


# ── Credential Repository Tests ─────────────────────────────────

@pytest.mark.asyncio
async def test_credential_metadata_persistence(session, secret_store):
    """Credential metadata should persist; actual secret goes to SecretStore."""
    from app.storage.repositories import ProviderRepository, CredentialRepository

    provider = await ProviderRepository.create(
        session,
        name="Test",
        protocol="openai-completions",
        base_url="https://api.example.com",
    )

    # Store actual secret in SecretStore
    secret_ref = secret_store.store("sk-actual-secret-value-12345")

    # Store metadata in database (no actual secret)
    cred = await CredentialRepository.create(
        session,
        provider_id=provider.id,
        key_masked="************345",
        secret_ref=secret_ref,
        source="manual",
    )
    assert cred.key_masked == "************345"
    assert cred.state == "active"

    # Retrieve secret from SecretStore using the reference
    retrieved_secret = secret_store.retrieve(secret_ref)
    assert retrieved_secret == "sk-actual-secret-value-12345"

    # Get active credential
    active = await CredentialRepository.get_active(session, provider.id)
    assert active is not None
    assert active.id == cred.id

    # Deactivate
    await CredentialRepository.deactivate(session, cred.id)
    active = await CredentialRepository.get_active(session, provider.id)
    assert active is None


# ── Session Repository Tests ────────────────────────────────────

@pytest.mark.asyncio
async def test_session_metadata_persistence(session, secret_store):
    """Session metadata should persist; actual value goes to SecretStore."""
    from app.storage.repositories import ProviderRepository, SessionRepository

    provider = await ProviderRepository.create(
        session,
        name="Test",
        protocol="openai-completions",
        base_url="https://api.example.com",
    )

    secret_ref = secret_store.store("session_cookie_value_abc123")

    sess = await SessionRepository.create(
        session,
        provider_id=provider.id,
        session_masked="session=****c123",
        secret_ref=secret_ref,
    )
    assert sess.status == "unknown"

    # Update status
    await SessionRepository.update_fields(session, sess.id, status="valid")
    fetched = await SessionRepository.get_by_id(session, sess.id)
    assert fetched.status == "valid"

    # Get by provider
    by_provider = await SessionRepository.get_by_provider(session, provider.id)
    assert by_provider is not None
    assert by_provider.id == sess.id


# ── Credential Event Repository Tests ────────────────────────────

@pytest.mark.asyncio
async def test_credential_event_persistence(session):
    """Credential events should persist with all event types."""
    from app.storage.repositories import CredentialEventRepository

    # Create events with different event types
    for event_type in [
        "created", "imported_manually", "validated", "activated",
        "deactivated", "expired", "invalid", "replacement_requested",
        "replacement_completed", "warning_triggered", "provider_assisted_rotation",
    ]:
        await CredentialEventRepository.create(
            session,
            event_type=event_type,
            status="success",
            duration_ms=1500,
        )

    events = await CredentialEventRepository.list_recent(session)
    assert len(events) == 11

    # Verify event types are distinct
    types = {e.event_type for e in events}
    assert types == {
        "created", "imported_manually", "validated", "activated",
        "deactivated", "expired", "invalid", "replacement_requested",
        "replacement_completed", "warning_triggered", "provider_assisted_rotation",
    }


# ── Usage Repository Tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_usage_snapshot_persistence(session):
    """Usage snapshots should persist and be retrievable."""
    from app.storage.repositories import UsageRepository

    await UsageRepository.create(
        session,
        input_tokens=1000,
        output_tokens=500,
        total_tokens=1500,
        remaining=1498500,
        limit=1500000,
    )
    await UsageRepository.create(
        session,
        input_tokens=2000,
        output_tokens=1000,
        total_tokens=3000,
        remaining=1497000,
        limit=1500000,
    )

    latest = await UsageRepository.get_latest(session)
    assert latest is not None
    assert latest.total_tokens == 3000

    recent = await UsageRepository.list_recent(session, limit=10)
    assert len(recent) == 2


# ── Event Repository Tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_event_persistence(session):
    """Events should persist with type, severity, and details."""
    from app.storage.repositories import EventRepository

    await EventRepository.create(
        session,
        event_type="gateway.started",
        message="Gateway started on port 5800",
        severity="info",
    )
    await EventRepository.create(
        session,
        event_type="rotation.failed",
        message="Rotation failed: session expired",
        severity="error",
        details_json=json.dumps({"error": "401 Unauthorized"}),
    )

    events = await EventRepository.list_recent(session)
    assert len(events) == 2

    by_type = await EventRepository.list_by_type(session, "rotation.failed")
    assert len(by_type) == 1
    assert by_type[0].severity == "error"


# ── Health Repository Tests ─────────────────────────────────────

@pytest.mark.asyncio
async def test_health_check_persistence(session):
    """Health checks should persist with target type and status."""
    from app.storage.repositories import HealthRepository

    await HealthRepository.create(
        session,
        target_type="gateway",
        target_id="local",
        status="healthy",
        latency_ms=2.5,
    )
    await HealthRepository.create(
        session,
        target_type="provider",
        target_id="prov123",
        status="degraded",
        latency_ms=450.0,
        details_json=json.dumps({"error": "slow response"}),
    )

    latest_gateway = await HealthRepository.get_latest(session, "gateway", "local")
    assert latest_gateway is not None
    assert latest_gateway.status == "healthy"

    latest_provider = await HealthRepository.get_latest(session, "provider", "prov123")
    assert latest_provider.status == "degraded"

    all_checks = await HealthRepository.list_recent(session)
    assert len(all_checks) == 2


# ── Settings Repository Tests ───────────────────────────────────

@pytest.mark.asyncio
async def test_settings_persistence(session):
    """Settings should support get, set, list, delete."""
    from app.storage.repositories import SettingsRepository

    # Set
    await SettingsRepository.set(session, "rotation_threshold_pct", "90")
    await SettingsRepository.set(session, "auto_restart", "true")

    # Get
    val = await SettingsRepository.get(session, "rotation_threshold_pct")
    assert val == "90"

    # List
    all_settings = await SettingsRepository.list_all(session)
    assert all_settings["rotation_threshold_pct"] == "90"
    assert all_settings["auto_restart"] == "true"

    # Update
    await SettingsRepository.set(session, "rotation_threshold_pct", "85")
    val = await SettingsRepository.get(session, "rotation_threshold_pct")
    assert val == "85"

    # Delete
    deleted = await SettingsRepository.delete(session, "auto_restart")
    assert deleted is True
    val = await SettingsRepository.get(session, "auto_restart")
    assert val is None


# ── Secret Store Tests ──────────────────────────────────────────

def test_secret_store_roundtrip(secret_store):
    """Store, retrieve, check existence, and delete a secret."""
    ref = secret_store.store("my-api-key-12345")
    assert ref is not None
    assert len(ref) == 16

    assert secret_store.exists(ref) is True
    assert secret_store.retrieve(ref) == "my-api-key-12345"

    assert secret_store.delete(ref) is True
    assert secret_store.exists(ref) is False
    assert secret_store.retrieve(ref) is None


def test_secret_store_nonexistent(secret_store):
    """Retrieving a nonexistent secret should return None."""
    assert secret_store.retrieve("nonexistent") is None
    assert secret_store.exists("nonexistent") is False
    assert secret_store.delete("nonexistent") is False


def test_secret_store_masked_display(secret_store):
    """The masked value should be derivable from the secret."""
    secret = "sk-abcdefghijklmnop"
    masked = secret[-4:].rjust(len(secret), "*")
    # "sk-abcdefghijklmnop" is 19 chars, last 4 = "mnop", so 15 stars + mnop
    assert masked == "***************mnop"

    ref = secret_store.store(secret)
    retrieved = secret_store.retrieve(ref)
    assert retrieved == secret
