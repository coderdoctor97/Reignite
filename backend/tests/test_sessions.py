"""
Tests for SessionManager, session validator abstraction, session provider
adapter, and session API routes.

Phase 4.1: Tests for manual entry, listing, retrieval, active lookup,
masking, SecretStore usage, lifecycle, activation, deactivation,
replacement, validation success/failure/unknown, expired/invalid states,
event creation, duplicate-safe replacement, and secret non-leakage.

Uses fake/mock validators. No real external services are called.
"""

import pytest
import json

from httpx import AsyncClient, ASGITransport


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _configure_for_test(tmp_path, monkeypatch):
    """Set up isolated test environment."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("GCC_DATABASE_PATH", db_path)
    monkeypatch.setenv("GCC_LEGACY_BASE_DIR", str(tmp_path / "legacy"))
    monkeypatch.setenv("GCC_GATEWAY_PORT", "15800")
    monkeypatch.setenv("GCC_SESSION_VALIDATION_INTERVAL", "3600")

    from app.core.config import get_settings
    get_settings.cache_clear()

    import app.storage.database as db_mod
    db_mod._engine = None
    db_mod._session_factory = None

    # Reset singletons
    import app.core.secrets as secrets_mod
    secrets_mod._store = None

    import app.services.session_manager as sm_mod
    sm_mod._session_manager = None

    import app.adapters.session_provider_adapter as spa_mod
    spa_mod._adapter = None

    yield

    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._session_factory = None
    secrets_mod._store = None
    sm_mod._session_manager = None
    spa_mod._adapter = None


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
            auth_type="session-cookie",
            provider_id="testprov01",
        )
        await session.commit()
        return provider


# ── Helpers ──────────────────────────────────────────────────────

TEST_SESSION = "opus_session_mR6OoW3bZDW1WYsprLt1234567890abcdef"

PROVIDER_ID = "testprov01"


def make_fake_validator(result_status="unknown", error=None):
    """Build a fake SessionValidator with a fixed (or mutable) result.

    Returns (validator, result_holder). Mutating result_holder['status']
    changes what the validator returns on subsequent calls.
    """
    from app.services.session_manager import SessionValidationResult

    holder = {"status": result_status, "error": error}

    class FakeValidator:
        calls = 0

        async def validate(self, session, secret_value=None):
            FakeValidator.calls += 1
            holder["last_secret"] = secret_value
            return SessionValidationResult(status=holder["status"], error=holder["error"])

    return FakeValidator(), holder


async def add_session(client, value=TEST_SESSION, provider_id=PROVIDER_ID, label=None):
    """Helper: POST a new session and return its JSON body."""
    body = {"secret_value": value, "provider_id": provider_id}
    if label:
        body["label"] = label
    resp = await client.post("/api/sessions", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ── 1. Create session ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_add_session(client, default_provider):
    """Adding a session should store metadata and return masked value."""
    data = await add_session(client)
    assert data["provider_id"] == PROVIDER_ID
    assert data["lifecycle_state"] == "inactive"
    assert data["validation_state"] == "unknown"
    assert data["source"] == "manual"
    assert data["session_masked"] is not None
    assert data["session_masked"] != TEST_SESSION
    assert "id" in data
    # No raw secret / secret ref in the response
    assert "secret_ref" not in data
    assert TEST_SESSION not in json.dumps(data)


@pytest.mark.asyncio
async def test_add_session_with_label(client, default_provider):
    """Optional label should be preserved."""
    data = await add_session(client, label="my dashboard session")
    assert data["label"] == "my dashboard session"


@pytest.mark.asyncio
async def test_add_session_empty_value(client, default_provider):
    """Adding an empty session secret should fail."""
    resp = await client.post("/api/sessions", json={
        "secret_value": "",
        "provider_id": PROVIDER_ID,
    })
    assert resp.status_code == 422  # Pydantic validation


@pytest.mark.asyncio
async def test_add_session_unknown_provider(client):
    """Adding a session for a nonexistent provider should fail."""
    resp = await client.post("/api/sessions", json={
        "secret_value": TEST_SESSION,
        "provider_id": "nope",
    })
    assert resp.status_code == 400


# ── 2. List sessions ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_sessions(client, default_provider):
    """Listing sessions should return all sessions."""
    await add_session(client, value="sess-one-abcdefghijklmnop")
    await add_session(client, value="sess-two-qrstuvwxyz123456")

    resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["sessions"]) == 2
    for s in data["sessions"]:
        assert "secret_ref" not in s


@pytest.mark.asyncio
async def test_list_sessions_filter_by_provider(client, default_provider):
    """Listing with provider filter should return only that provider's sessions."""
    from app.storage.database import get_async_session
    from app.storage.repositories import ProviderRepository
    async with get_async_session() as session:
        await ProviderRepository.create(
            session, name="Other", protocol="openai-completions",
            base_url="https://other.example.com", auth_type="api-key",
            provider_id="otherprov",
        )
        await session.commit()

    await add_session(client, provider_id=PROVIDER_ID)
    await add_session(client, provider_id="otherprov")

    resp = await client.get(f"/api/sessions?provider_id={PROVIDER_ID}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["sessions"][0]["provider_id"] == PROVIDER_ID


# ── 3. Get session ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_session_by_id(client, default_provider):
    """Getting a session by ID should return its safe metadata."""
    created = await add_session(client)
    resp = await client.get(f"/api/sessions/{created['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == created["id"]
    assert data["session_masked"] is not None
    assert TEST_SESSION not in resp.text


@pytest.mark.asyncio
async def test_get_nonexistent_session(client):
    """Getting a nonexistent session should return 404."""
    resp = await client.get("/api/sessions/nonexistent")
    assert resp.status_code == 404


# ── 4. Active session lookup ─────────────────────────────────────

@pytest.mark.asyncio
async def test_get_active_session_none(client):
    """Getting the active session when none exists should return null."""
    resp = await client.get("/api/sessions/active")
    assert resp.status_code == 200
    assert resp.json() is None


@pytest.mark.asyncio
async def test_get_active_session_after_activation(client, default_provider):
    """After activation, the active endpoint should return that session."""
    created = await add_session(client)
    await client.post(f"/api/sessions/{created['id']}/activate")

    resp = await client.get("/api/sessions/active")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]
    assert resp.json()["lifecycle_state"] == "active"

    # Provider-scoped lookup
    resp = await client.get(f"/api/sessions/active?provider_id={PROVIDER_ID}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


# ── 5. Masking ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_masking(client, default_provider):
    """Session display should use masking — never the full value."""
    data = await add_session(client)
    masked = data["session_masked"]
    assert TEST_SESSION not in masked
    assert "*" in masked
    assert masked.endswith(TEST_SESSION[-4:])


@pytest.mark.asyncio
async def test_short_secret_masking(client, default_provider):
    """Short secrets should be fully masked."""
    data = await add_session(client, value="abcd")
    assert data["session_masked"] == "****"


# ── 6. SecretStore usage ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_secret_stored_in_secret_store(client, default_provider):
    """The session secret should live in the SecretStore, not the database."""
    from app.core.secrets import get_secret_store
    from app.storage.database import get_async_session
    from app.storage.models import SessionRow
    from sqlalchemy import select

    created = await add_session(client)

    async with get_async_session() as session:
        row = (await session.execute(
            select(SessionRow).where(SessionRow.id == created["id"])
        )).scalar_one()
        ref = row.secret_ref
        assert ref is not None

        # No database column contains the raw secret
        for col in (
            row.id, row.provider_id, row.label, row.session_masked,
            row.secret_ref, row.source, row.lifecycle_state,
            row.validation_state, row.last_validated, row.next_validation_at,
            row.last_validation_error, row.last_successful_fetch,
            row.metadata_json, row.created_at, row.updated_at,
        ):
            assert TEST_SESSION not in (col or "")

    store = get_secret_store()
    assert store.retrieve(ref) == TEST_SESSION


@pytest.mark.asyncio
async def test_missing_secret_validation_invalid(client, default_provider):
    """A session whose secret is missing from the store is 'invalid'."""
    from app.core.secrets import get_secret_store
    from app.storage.database import get_async_session
    from app.storage.repositories import SessionRepository

    created = await add_session(client)

    # Delete the secret out from under the session
    async with get_async_session() as session:
        row = await SessionRepository.get_by_id(session, created["id"])
        get_secret_store().delete(row.secret_ref)

    resp = await client.post(f"/api/sessions/{created['id']}/validate")
    assert resp.status_code == 200
    assert resp.json()["session"]["validation_state"] == "invalid"


# ── 7/8/9. Lifecycle: activate / deactivate ──────────────────────

@pytest.mark.asyncio
async def test_session_lifecycle_transitions(client, default_provider):
    """Full add → activate → deactivate lifecycle should work."""
    created = await add_session(client)
    assert created["lifecycle_state"] == "inactive"

    resp = await client.post(f"/api/sessions/{created['id']}/activate")
    assert resp.status_code == 200
    assert resp.json()["session"]["lifecycle_state"] == "active"
    assert resp.json()["session"]["activated_at"] is not None

    resp = await client.post(f"/api/sessions/{created['id']}/deactivate")
    assert resp.status_code == 200
    assert resp.json()["session"]["lifecycle_state"] == "inactive"
    assert resp.json()["session"]["deactivated_at"] is not None

    # Deactivation is idempotent
    resp = await client.post(f"/api/sessions/{created['id']}/deactivate")
    assert resp.status_code == 200
    assert resp.json()["session"]["lifecycle_state"] == "inactive"


@pytest.mark.asyncio
async def test_activate_deactivates_previous(client, default_provider):
    """Activating a new session should deactivate the previous one."""
    s1 = await add_session(client, value="sess-first-abcdefghij")
    s2 = await add_session(client, value="sess-second-klmnopqrst")
    await client.post(f"/api/sessions/{s1['id']}/activate")
    await client.post(f"/api/sessions/{s2['id']}/activate")

    assert (await client.get(f"/api/sessions/{s1['id']}")).json()["lifecycle_state"] == "inactive"
    assert (await client.get(f"/api/sessions/{s2['id']}")).json()["lifecycle_state"] == "active"

    # Only one active session for the provider
    resp = await client.get(f"/api/sessions?provider_id={PROVIDER_ID}")
    active = [s for s in resp.json()["sessions"] if s["lifecycle_state"] == "active"]
    assert len(active) == 1
    assert active[0]["id"] == s2["id"]


@pytest.mark.asyncio
async def test_activate_nonexistent(client):
    """Activating a nonexistent session should return 404."""
    resp = await client.post("/api/sessions/nonexistent/activate")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_deactivate_nonexistent(client):
    """Deactivating a nonexistent session should return 404."""
    resp = await client.post("/api/sessions/nonexistent/deactivate")
    assert resp.status_code == 404


# ── 10. Replace session ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_replace_session(client, default_provider):
    """Replacing should add the new session, activate it, and deactivate the old."""
    s1 = await add_session(client, value="sess-old-abcdefghijklmn")
    await client.post(f"/api/sessions/{s1['id']}/activate")

    new_value = "sess-new-qrstuvwxyz987654"
    resp = await client.post("/api/sessions/replace", json={
        "secret_value": new_value,
        "provider_id": PROVIDER_ID,
        "label": "replacement",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["session"]["lifecycle_state"] == "active"
    new_id = data["session"]["id"]
    assert new_id != s1["id"]
    assert data["session"]["label"] == "replacement"

    # Previous session preserved as inactive, not deleted
    old = (await client.get(f"/api/sessions/{s1['id']}")).json()
    assert old["lifecycle_state"] == "inactive"

    # New session active and its secret stored
    from app.core.secrets import get_secret_store
    from app.storage.database import get_async_session
    from app.storage.repositories import SessionRepository
    async with get_async_session() as session:
        row = await SessionRepository.get_by_id(session, new_id)
        assert get_secret_store().retrieve(row.secret_ref) == new_value


@pytest.mark.asyncio
async def test_replace_session_explicit_target(client, default_provider):
    """Replacing with an explicit session_id should target that session."""
    s1 = await add_session(client, value="sess-one-abcdefghijklmn")
    s2 = await add_session(client, value="sess-two-opqrstuvwxyz12")
    await client.post(f"/api/sessions/{s1['id']}/activate")
    await client.post(f"/api/sessions/{s2['id']}/activate")  # s1 → inactive

    resp = await client.post("/api/sessions/replace", json={
        "secret_value": "sess-three-34567890abcdef",
        "provider_id": PROVIDER_ID,
        "session_id": s1["id"],
    })
    assert resp.status_code == 200
    assert (await client.get(f"/api/sessions/{s1['id']}")).json()["lifecycle_state"] == "inactive"
    # s2 (the active one) is deactivated when the replacement activates
    assert (await client.get(f"/api/sessions/{s2['id']}")).json()["lifecycle_state"] == "inactive"


@pytest.mark.asyncio
async def test_replace_session_no_existing_session(client, default_provider):
    """Replacing when no session exists should fail cleanly."""
    resp = await client.post("/api/sessions/replace", json={
        "secret_value": TEST_SESSION,
        "provider_id": PROVIDER_ID,
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_replace_session_validation_failure_leaves_previous_active(
    client, default_provider
):
    """A replacement that fails validation must not disturb the previous session."""
    from app.services.session_manager import SessionManager, set_session_manager
    from app.core.secrets import get_secret_store

    s1 = await add_session(client, value="sess-original-abcdefghij")
    await client.post(f"/api/sessions/{s1['id']}/activate")

    # Install a fake validator that rejects everything
    validator, _ = make_fake_validator(result_status="invalid", error="rejected")
    set_session_manager(SessionManager(secret_store=get_secret_store(), validator=validator))

    resp = await client.post("/api/sessions/replace", json={
        "secret_value": "sess-bad-new-1234567890",
        "provider_id": PROVIDER_ID,
    })
    assert resp.status_code == 400
    assert "rejected" in resp.json()["detail"]

    # Previous session still active
    assert (await client.get(f"/api/sessions/{s1['id']}")).json()["lifecycle_state"] == "active"

    # The failed replacement session was created but is invalid, not active
    all_sessions = (await client.get("/api/sessions")).json()["sessions"]
    failed = [s for s in all_sessions if s["session_masked"] and s["id"] != s1["id"]]
    assert len(failed) == 1
    assert failed[0]["lifecycle_state"] == "invalid"
    assert failed[0]["validation_state"] == "invalid"


# ── 11/12/13. Validation abstraction ─────────────────────────────

@pytest.mark.asyncio
async def test_validation_success_abstraction(client, default_provider):
    """A validator reporting 'valid' should yield valid + healthy."""
    from app.services.session_manager import SessionManager, set_session_manager
    from app.core.secrets import get_secret_store

    validator, holder = make_fake_validator(result_status="valid")
    set_session_manager(SessionManager(secret_store=get_secret_store(), validator=validator))

    created = await add_session(client)
    await client.post(f"/api/sessions/{created['id']}/activate")

    resp = await client.post(f"/api/sessions/{created['id']}/validate")
    assert resp.status_code == 200
    data = resp.json()["session"]
    assert data["validation_state"] == "valid"
    assert data["health"] == "healthy"
    assert data["last_validated"] is not None
    assert data["next_validation_at"] is not None
    # The validator received the actual secret value
    assert holder["last_secret"] == TEST_SESSION


@pytest.mark.asyncio
async def test_validation_failure_abstraction(client, default_provider):
    """A validator reporting 'invalid' should yield invalid + critical."""
    from app.services.session_manager import SessionManager, set_session_manager
    from app.core.secrets import get_secret_store

    validator, _ = make_fake_validator(result_status="invalid", error="Provider rejected session")
    set_session_manager(SessionManager(secret_store=get_secret_store(), validator=validator))

    created = await add_session(client)
    resp = await client.post(f"/api/sessions/{created['id']}/validate")
    assert resp.status_code == 200
    data = resp.json()["session"]
    assert data["validation_state"] == "invalid"
    assert data["lifecycle_state"] == "invalid"
    assert data["health"] == "critical"
    assert data["last_validation_error"] == "Provider rejected session"


@pytest.mark.asyncio
async def test_validation_unknown_by_default(client, default_provider):
    """Without a provider adapter, validation must honestly report 'unknown'.

    A session must never be reported valid merely because it exists in
    the SecretStore.
    """
    created = await add_session(client)
    resp = await client.post(f"/api/sessions/{created['id']}/validate")
    assert resp.status_code == 200
    data = resp.json()["session"]
    assert data["validation_state"] == "unknown"
    assert data["health"] == "unknown"
    assert data["last_validated"] is not None
    assert data["next_validation_at"] is not None


@pytest.mark.asyncio
async def test_validation_unavailable(client, default_provider):
    """A validator reporting 'unavailable' should yield health 'warning'."""
    from app.services.session_manager import SessionManager, set_session_manager
    from app.core.secrets import get_secret_store

    validator, _ = make_fake_validator(result_status="unavailable", error="Endpoint down")
    set_session_manager(SessionManager(secret_store=get_secret_store(), validator=validator))

    created = await add_session(client)
    resp = await client.post(f"/api/sessions/{created['id']}/validate")
    data = resp.json()["session"]
    assert data["validation_state"] == "unavailable"
    assert data["health"] == "warning"


@pytest.mark.asyncio
async def test_validation_error_state(client, default_provider):
    """A validator reporting 'error' should yield health 'warning'."""
    from app.services.session_manager import SessionManager, set_session_manager
    from app.core.secrets import get_secret_store

    validator, _ = make_fake_validator(result_status="error", error="Adapter crashed")
    set_session_manager(SessionManager(secret_store=get_secret_store(), validator=validator))

    created = await add_session(client)
    resp = await client.post(f"/api/sessions/{created['id']}/validate")
    data = resp.json()["session"]
    assert data["validation_state"] == "error"
    assert data["health"] == "warning"


# ── 14/15. Expired / invalid states ──────────────────────────────

@pytest.mark.asyncio
async def test_expired_state(client, default_provider):
    """A validator reporting 'expired' should yield expired + critical."""
    from app.services.session_manager import SessionManager, set_session_manager
    from app.core.secrets import get_secret_store

    validator, _ = make_fake_validator(result_status="expired", error="Cookie expired")
    set_session_manager(SessionManager(secret_store=get_secret_store(), validator=validator))

    created = await add_session(client)
    resp = await client.post(f"/api/sessions/{created['id']}/validate")
    data = resp.json()["session"]
    assert data["validation_state"] == "expired"
    assert data["lifecycle_state"] == "expired"
    assert data["health"] == "critical"


@pytest.mark.asyncio
async def test_activate_expired_session_conflict(client, default_provider):
    """Activating an expired session should be refused with 409."""
    from app.services.session_manager import SessionManager, set_session_manager
    from app.core.secrets import get_secret_store

    validator, _ = make_fake_validator(result_status="expired")
    set_session_manager(SessionManager(secret_store=get_secret_store(), validator=validator))

    created = await add_session(client)
    await client.post(f"/api/sessions/{created['id']}/validate")
    assert (await client.get(f"/api/sessions/{created['id']}")).json()["lifecycle_state"] == "expired"

    resp = await client.post(f"/api/sessions/{created['id']}/activate")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_activate_invalid_session_conflict(client, default_provider):
    """Activating an invalid session should be refused with 409."""
    from app.services.session_manager import SessionManager, set_session_manager
    from app.core.secrets import get_secret_store

    validator, _ = make_fake_validator(result_status="invalid")
    set_session_manager(SessionManager(secret_store=get_secret_store(), validator=validator))

    created = await add_session(client)
    await client.post(f"/api/sessions/{created['id']}/validate")

    resp = await client.post(f"/api/sessions/{created['id']}/activate")
    assert resp.status_code == 409
    assert "replace" in resp.json()["detail"].lower()


# ── 16. Event creation ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_events_recorded(client, default_provider):
    """Session operations should record events in session_events."""
    from app.storage.database import get_async_session
    from app.storage.repositories import SessionEventRepository

    created = await add_session(client)
    sid = created["id"]
    await client.post(f"/api/sessions/{sid}/validate")
    await client.post(f"/api/sessions/{sid}/activate")
    await client.post(f"/api/sessions/{sid}/deactivate")

    async with get_async_session() as session:
        events = await SessionEventRepository.list_by_session(session, sid)
        event_types = [e.event_type for e in events]
        for expected in ("created", "imported_manually", "validated", "activated", "deactivated"):
            assert expected in event_types


@pytest.mark.asyncio
async def test_replacement_events_recorded(client, default_provider):
    """Replacement should record replacement_requested and replacement_completed."""
    from app.storage.database import get_async_session
    from app.storage.repositories import SessionEventRepository

    s1 = await add_session(client, value="sess-old-abcdefghijklmn")
    await client.post(f"/api/sessions/{s1['id']}/activate")

    resp = await client.post("/api/sessions/replace", json={
        "secret_value": "sess-new-qrstuvwxyz1234",
        "provider_id": PROVIDER_ID,
    })
    new_id = resp.json()["session"]["id"]

    async with get_async_session() as session:
        events = await SessionEventRepository.list_recent(session, limit=30)
        event_types = [e.event_type for e in events]
        assert "replacement_requested" in event_types
        assert "replacement_completed" in event_types
        # The request targets the OLD session; completion references the NEW one
        requested = [e for e in events if e.event_type == "replacement_requested"][0]
        assert requested.session_id == s1["id"]
        completed = [e for e in events if e.event_type == "replacement_completed"][0]
        assert completed.session_id == new_id


@pytest.mark.asyncio
async def test_app_events_recorded(client, default_provider):
    """App-wide structured events should use session.* types."""
    from app.storage.database import get_async_session
    from app.storage.repositories import EventRepository

    created = await add_session(client)
    await client.post(f"/api/sessions/{created['id']}/activate")

    async with get_async_session() as session:
        events = await EventRepository.list_recent(session, limit=50)
        types = [e.event_type for e in events]
        assert "session.created" in types
        assert "session.activated" in types


# ── 17. Duplicate-safe replacement behavior ──────────────────────

@pytest.mark.asyncio
async def test_replacement_is_duplicate_safe(client, default_provider):
    """Two sequential replacements must leave exactly one active session."""
    s1 = await add_session(client, value="sess-gen-1-abcdefghijklmn")
    await client.post(f"/api/sessions/{s1['id']}/activate")

    r1 = await client.post("/api/sessions/replace", json={
        "secret_value": "sess-gen-2-opqrstuvwxyzab",
        "provider_id": PROVIDER_ID,
    })
    assert r1.status_code == 200
    id2 = r1.json()["session"]["id"]

    r2 = await client.post("/api/sessions/replace", json={
        "secret_value": "sess-gen-3-1234567890abcd",
        "provider_id": PROVIDER_ID,
    })
    assert r2.status_code == 200
    id3 = r2.json()["session"]["id"]

    sessions = (await client.get(f"/api/sessions?provider_id={PROVIDER_ID}")).json()["sessions"]
    active = [s for s in sessions if s["lifecycle_state"] == "active"]
    assert len(active) == 1
    assert active[0]["id"] == id3
    assert {s["id"] for s in sessions} == {s1["id"], id2, id3}
    # Every session kept, all predecessors inactive
    assert all(s["lifecycle_state"] == "inactive" for s in sessions if s["id"] != id3)

    # Both replacements recorded audit events
    from app.storage.database import get_async_session
    from app.storage.repositories import SessionEventRepository
    async with get_async_session() as session:
        events = await SessionEventRepository.list_recent(session, limit=50)
        completed = [e for e in events if e.event_type == "replacement_completed"]
        assert len(completed) == 2


# ── 18. Secret non-leakage ───────────────────────────────────────

@pytest.mark.asyncio
async def test_no_secrets_in_list_response(client, default_provider):
    """The list endpoint must never contain raw session secrets."""
    await add_session(client)
    resp = await client.get("/api/sessions")
    assert TEST_SESSION not in resp.text


@pytest.mark.asyncio
async def test_no_secrets_in_get_response(client, default_provider):
    """The get endpoint must never contain raw session secrets."""
    created = await add_session(client)
    resp = await client.get(f"/api/sessions/{created['id']}")
    assert TEST_SESSION not in resp.text


@pytest.mark.asyncio
async def test_no_secrets_in_action_responses(client, default_provider):
    """Activate/deactivate/validate/replace must never leak the secret."""
    created = await add_session(client)

    resp = await client.post(f"/api/sessions/{created['id']}/validate")
    assert TEST_SESSION not in resp.text
    resp = await client.post(f"/api/sessions/{created['id']}/activate")
    assert TEST_SESSION not in resp.text
    resp = await client.post(f"/api/sessions/{created['id']}/deactivate")
    assert TEST_SESSION not in resp.text

    new_secret = "sess-ultra-secret-9876543210"
    resp = await client.post("/api/sessions/replace", json={
        "secret_value": new_secret,
        "provider_id": PROVIDER_ID,
    })
    assert new_secret not in resp.text
    assert TEST_SESSION not in resp.text


@pytest.mark.asyncio
async def test_no_secrets_in_health_response(client, default_provider):
    """The health endpoint must never contain raw session secrets."""
    await add_session(client)
    resp = await client.get("/api/sessions/health")
    assert TEST_SESSION not in resp.text


@pytest.mark.asyncio
async def test_no_secrets_in_session_events(client, default_provider):
    """Session events must never contain raw session secrets."""
    from app.storage.database import get_async_session
    from app.storage.repositories import SessionEventRepository

    created = await add_session(client)
    await client.post(f"/api/sessions/{created['id']}/validate")
    await client.post(f"/api/sessions/{created['id']}/activate")

    async with get_async_session() as session:
        events = await SessionEventRepository.list_by_session(session, created["id"])
        for event in events:
            assert TEST_SESSION not in (event.details_json or "")
            assert TEST_SESSION not in (event.failure_reason or "")


@pytest.mark.asyncio
async def test_no_secrets_in_app_events(client, default_provider):
    """App-wide events must never contain raw session secrets."""
    from app.storage.database import get_async_session
    from app.storage.repositories import EventRepository

    created = await add_session(client)
    await client.post(f"/api/sessions/{created['id']}/activate")

    async with get_async_session() as session:
        events = await EventRepository.list_recent(session, limit=50)
        for event in events:
            assert TEST_SESSION not in (event.details_json or "")
            assert TEST_SESSION not in event.message


@pytest.mark.asyncio
async def test_no_secrets_in_logs(client, default_provider, caplog):
    """Log output must never contain raw session secrets."""
    import logging
    caplog.set_level(logging.DEBUG, logger="gcc.session_manager")
    caplog.set_level(logging.DEBUG, logger="gcc.secrets")

    created = await add_session(client)
    await client.post(f"/api/sessions/{created['id']}/validate")
    await client.post(f"/api/sessions/{created['id']}/activate")

    assert TEST_SESSION not in caplog.text


# ── SessionManager direct tests ──────────────────────────────────

@pytest.mark.asyncio
async def test_manager_add_get_list(client, default_provider):
    """SessionManager should add and retrieve sessions."""
    from app.services.session_manager import get_session_manager

    manager = get_session_manager()
    created = await manager.add_session(TEST_SESSION, PROVIDER_ID, label="direct")
    assert created["lifecycle_state"] == "inactive"

    fetched = await manager.get_session(created["id"])
    assert fetched is not None
    assert fetched["id"] == created["id"]

    listed = await manager.list_sessions(provider_id=PROVIDER_ID)
    assert len(listed) == 1


@pytest.mark.asyncio
async def test_manager_active_lookup(client, default_provider):
    """get_active_session should track activation state."""
    from app.services.session_manager import get_session_manager

    manager = get_session_manager()
    s1 = await manager.add_session("sess-mgr-one-abcdefghijkl", PROVIDER_ID)
    s2 = await manager.add_session("sess-mgr-two-mnopqrstuvwx", PROVIDER_ID)

    assert await manager.get_active_session(PROVIDER_ID) is None

    await manager.activate_session(s1["id"])
    active = await manager.get_active_session(PROVIDER_ID)
    assert active["id"] == s1["id"]

    await manager.activate_session(s2["id"])
    assert (await manager.get_active_session(PROVIDER_ID))["id"] == s2["id"]
    assert (await manager.get_session(s1["id"]))["lifecycle_state"] == "inactive"


@pytest.mark.asyncio
async def test_manager_multiple_sessions_per_provider(client, default_provider):
    """Multiple sessions per provider are supported (no 1:1 assumption)."""
    from app.services.session_manager import get_session_manager

    manager = get_session_manager()
    ids = []
    for i in range(3):
        s = await manager.add_session(f"multi-session-{i}-abcdefghijklmnop", PROVIDER_ID)
        ids.append(s["id"])

    listed = await manager.list_sessions(provider_id=PROVIDER_ID)
    assert len(listed) == 3


# ── SessionProviderAdapter tests ─────────────────────────────────

@pytest.mark.asyncio
async def test_provider_adapter_returns_active_secret(client, default_provider):
    """The adapter should return the active session's raw secret."""
    from app.adapters.session_provider_adapter import get_session_provider_adapter

    created = await add_session(client)
    await client.post(f"/api/sessions/{created['id']}/activate")

    adapter = get_session_provider_adapter()
    secret = await adapter.get_active_session_secret(PROVIDER_ID)
    assert secret == TEST_SESSION

    # No active session → None
    await client.post(f"/api/sessions/{created['id']}/deactivate")
    assert await adapter.get_active_session_secret(PROVIDER_ID) is None


@pytest.mark.asyncio
async def test_secret_never_in_api_even_for_adapter(client, default_provider):
    """The API exposes no path that returns the raw session secret."""
    from app.adapters.session_provider_adapter import get_session_provider_adapter

    created = await add_session(client)
    await client.post(f"/api/sessions/{created['id']}/activate")

    # The adapter (internal only) can see it…
    adapter = get_session_provider_adapter()
    assert await adapter.get_active_session_secret(PROVIDER_ID) == TEST_SESSION

    # …but every HTTP endpoint still returns masked data only
    for path in (
        "/api/sessions",
        "/api/sessions/health",
        "/api/sessions/active",
        f"/api/sessions/{created['id']}",
    ):
        resp = await client.get(path)
        assert TEST_SESSION not in resp.text
        assert "secret_ref" not in resp.text


# ── Validation timing ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_next_validation_computed(client, default_provider):
    """Validation should schedule next_validation_at per configuration."""
    from datetime import datetime, timezone, timedelta

    created = await add_session(client)
    resp = await client.post(f"/api/sessions/{created['id']}/validate")
    data = resp.json()["session"]

    next_at = datetime.fromisoformat(data["next_validation_at"])
    now = datetime.now(timezone.utc)
    delta = (next_at - now).total_seconds()
    # GCC_SESSION_VALIDATION_INTERVAL=3600 (set in fixture)
    assert 3590 < delta <= 3600
    assert data["last_validated"] is not None


@pytest.mark.asyncio
async def test_health_overdue_validation(client, default_provider, monkeypatch):
    """A valid session whose next_validation_at is past due is 'warning'."""
    from app.services.session_manager import SessionManager, set_session_manager
    from app.core.secrets import get_secret_store
    from app.storage.database import get_async_session
    from app.storage.repositories import SessionRepository
    from datetime import datetime, timezone, timedelta

    validator, _ = make_fake_validator(result_status="valid")
    set_session_manager(SessionManager(secret_store=get_secret_store(), validator=validator))

    created = await add_session(client)
    await client.post(f"/api/sessions/{created['id']}/activate")
    await client.post(f"/api/sessions/{created['id']}/validate")
    assert (await client.get(f"/api/sessions/{created['id']}")).json()["health"] == "healthy"

    # Force next_validation_at into the past
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    async with get_async_session() as session:
        await SessionRepository.update_fields(session, created["id"], next_validation_at=past)
        await session.commit()

    health = (await client.get(f"/api/sessions/{created['id']}")).json()["health"]
    assert health == "warning"


# ── Alembic migration ────────────────────────────────────────────

def test_alembic_migration_and_legacy_status_mapping(tmp_path, monkeypatch):
    """The Phase 4.1 migration should upgrade/downgrade cleanly and map
    legacy 'status' values into 'validation_state'."""
    import sqlite3
    from pathlib import Path
    from alembic import command
    from alembic.config import Config

    db_path = str(tmp_path / "mig.db")
    monkeypatch.setenv("GCC_DATABASE_PATH", db_path)
    from app.core.config import get_settings
    get_settings.cache_clear()

    ini = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(ini))

    # 1. Build the pre-Phase-4.1 schema and insert a legacy session row
    command.upgrade(cfg, "f2d8a87a8b44")

    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO providers (id, name, protocol, base_url, auth_type, enabled, "
        "health_status, created_at, updated_at) "
        "VALUES ('prov1', 'P', 'openai-completions', 'https://x', 'session-cookie', 1, "
        "'unknown', 't', 't')"
    )
    conn.execute(
        "INSERT INTO sessions (id, provider_id, session_masked, secret_ref, status, "
        "created_at, updated_at) VALUES ('sess1', 'prov1', 'masked', 'ref1', 'expired', 't', 't')"
    )
    conn.commit()
    conn.close()

    # 2. Upgrade to head: legacy 'status' maps into validation_state
    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)")]
    assert "status" not in cols
    assert {"lifecycle_state", "validation_state", "next_validation_at", "label", "source"} <= set(cols)
    row = conn.execute(
        "SELECT lifecycle_state, validation_state, source FROM sessions WHERE id='sess1'"
    ).fetchone()
    assert row[0] == "inactive"   # no lifecycle info ever existed — explicit activation required
    assert row[1] == "expired"    # legacy validation-like value preserved
    assert row[2] == "manual"
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert "session_events" in tables
    conn.close()

    # 3. Downgrade restores the legacy shape
    command.downgrade(cfg, "f2d8a87a8b44")
    conn = sqlite3.connect(db_path)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)")]
    assert "status" in cols and "lifecycle_state" not in cols
    row = conn.execute("SELECT status FROM sessions WHERE id='sess1'").fetchone()
    assert row[0] == "expired"
    conn.close()
