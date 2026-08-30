"""
Tests for database initialization.
"""

import pytest
import os
from pathlib import Path
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Point the database to a temporary directory for each test."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("GCC_DATABASE_PATH", db_path)
    from app.core.config import get_settings
    get_settings.cache_clear()
    import app.storage.database as db_mod
    db_mod._engine = None
    db_mod._session_factory = None
    yield
    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._session_factory = None


@pytest.mark.asyncio
async def test_database_init_creates_tables():
    """init_database should create all expected tables."""
    from app.storage.database import init_database, get_async_session, close_database

    await init_database()

    async with get_async_session() as session:
        result = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        )
        tables = [row[0] for row in result.fetchall()]

    expected = [
        "credentials", "events", "health_checks", "models",
        "providers", "rotation_events", "sessions", "settings", "usage_snapshots",
    ]
    for t in expected:
        assert t in tables, f"Missing table: {t}"

    await close_database()


@pytest.mark.asyncio
async def test_settings_table_insert_and_read():
    """Should be able to insert and read from the settings table."""
    from app.storage.database import init_database, get_async_session, close_database

    await init_database()

    async with get_async_session() as session:
        from app.storage.models import _utcnow
        await session.execute(
            text("INSERT INTO settings (key, value, updated_at) VALUES (:key, :value, :ts)"),
            {"key": "test_key", "value": "test_value", "ts": _utcnow()},
        )
        await session.commit()

        result = await session.execute(
            text("SELECT value FROM settings WHERE key = :key"),
            {"key": "test_key"},
        )
        row = result.fetchone()
        assert row[0] == "test_value"

    await close_database()
