"""
SQLite database connection and initialization.

Uses aiosqlite for async access. The database file lives at the path
configured in Settings.database_path (default: <project_root>/data/gateway.db).

This module provides:
- get_connection(): async context manager for a database connection
- init_database(): create tables if they don't exist
- close_database(): clean shutdown
"""

from __future__ import annotations

import aiosqlite
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("database")

# Module-level connection reference for clean shutdown
_connection: aiosqlite.Connection | None = None


async def get_connection() -> aiosqlite.Connection:
    """Return a database connection, creating it if needed.

    The connection is kept open for the lifetime of the application
    to avoid repeated open/close overhead.
    """
    global _connection
    if _connection is None:
        settings = get_settings()
        settings.ensure_data_dir()
        _connection = await aiosqlite.connect(settings.database_path)
        _connection.row_factory = aiosqlite.Row
        # Enable WAL mode for better concurrent read performance
        await _connection.execute("PRAGMA journal_mode=WAL")
        await _connection.execute("PRAGMA foreign_keys=ON")
        logger.info("Database connected: %s", settings.database_path)
    return _connection


async def close_database() -> None:
    """Close the database connection cleanly."""
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
        logger.info("Database connection closed")


async def init_database() -> None:
    """Create all tables if they don't exist.

    This is the Phase 1.1 minimal schema — just enough to prove
    the database layer works. Future phases will add the full schema
    via migrations.
    """
    db = await get_connection()

    # ── Settings table (key-value store for app configuration) ───
    await db.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Events table (structured log entries) ────────────────────
    await db.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type  TEXT NOT NULL,
            severity    TEXT DEFAULT 'info',
            message     TEXT NOT NULL,
            details     TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Providers table (placeholder for Phase 2+) ──────────────
    await db.execute("""
        CREATE TABLE IF NOT EXISTS providers (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            protocol        TEXT NOT NULL,
            base_url        TEXT NOT NULL,
            auth_type       TEXT NOT NULL DEFAULT 'api-key',
            enabled         INTEGER DEFAULT 1,
            health_status   TEXT DEFAULT 'unknown',
            last_health_check TIMESTAMP,
            metadata        TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── Models table (placeholder for Phase 4+) ─────────────────
    await db.execute("""
        CREATE TABLE IF NOT EXISTS models (
            id              TEXT PRIMARY KEY,
            provider_id     TEXT NOT NULL REFERENCES providers(id),
            display_name    TEXT NOT NULL,
            model_id        TEXT NOT NULL,
            context_window  INTEGER,
            capabilities    TEXT,
            enabled         INTEGER DEFAULT 1,
            is_default      INTEGER DEFAULT 0,
            is_fallback     INTEGER DEFAULT 0,
            metadata        TEXT,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    await db.commit()
    logger.info("Database tables initialized")
