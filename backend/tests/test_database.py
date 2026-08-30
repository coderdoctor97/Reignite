"""
Tests for database initialization and basic operations.
"""

import pytest
import aiosqlite
import tempfile
import os

from app.core.config import Settings


@pytest.mark.asyncio
async def test_database_init_creates_tables(tmp_path):
    """init_database should create the expected tables."""
    db_path = str(tmp_path / "test.db")

    # Override settings for this test
    import app.core.config as config_module
    original = config_module.get_settings.cache_info
    config_module.get_settings.cache_clear()
    os.environ["GCC_DATABASE_PATH"] = db_path
    try:
        from app.storage.database import init_database, close_database, get_connection
        await init_database()

        # Verify tables exist
        db = await get_connection()
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        rows = await cursor.fetchall()
        table_names = [row[0] for row in rows]

        assert "settings" in table_names
        assert "events" in table_names
        assert "providers" in table_names
        assert "models" in table_names

        await close_database()
    finally:
        del os.environ["GCC_DATABASE_PATH"]
        config_module.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_settings_table_insert_and_read(tmp_path):
    """Should be able to insert and read from the settings table."""
    db_path = str(tmp_path / "test.db")

    import app.core.config as config_module
    config_module.get_settings.cache_clear()
    os.environ["GCC_DATABASE_PATH"] = db_path
    try:
        from app.storage.database import init_database, close_database, get_connection
        await init_database()

        db = await get_connection()
        await db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("test_key", "test_value"),
        )
        await db.commit()

        cursor = await db.execute(
            "SELECT value FROM settings WHERE key = ?", ("test_key",)
        )
        row = await cursor.fetchone()
        assert row[0] == "test_value"

        await close_database()
    finally:
        del os.environ["GCC_DATABASE_PATH"]
        config_module.get_settings.cache_clear()
