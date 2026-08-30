"""
SQLite database connection and initialization.

Provides two access patterns:
1. SQLAlchemy async engine + sessions (for repositories)
2. Raw aiosqlite connection (for direct queries if needed)

The database file lives at the path configured in Settings.database_path.
"""

from __future__ import annotations

from pathlib import Path
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import aiosqlite
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
)

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("database")

# Module-level references for clean shutdown
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    """Return or create the SQLAlchemy async engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.ensure_data_dir()
        url = f"sqlite+aiosqlite:///{settings.database_path}"
        _engine = create_async_engine(
            url,
            echo=False,
            connect_args={"check_same_thread": False},
        )

        # Enable foreign keys for SQLite (required for CASCADE deletes)
        from sqlalchemy import event

        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        logger.info("Database engine created: %s", settings.database_path)
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return or create the session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            _get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for a database session.

    Usage:
        async with get_async_session() as session:
            result = await session.execute(select(ProviderRow))
    """
    factory = _get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_connection() -> aiosqlite.Connection:
    """Return a raw aiosqlite connection (for direct queries if needed).

    Prefer get_async_session() for repository operations.
    """
    settings = get_settings()
    settings.ensure_data_dir()
    conn = await aiosqlite.connect(settings.database_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    return conn


async def close_database() -> None:
    """Close the database engine cleanly."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database engine closed")


async def init_database() -> None:
    """Create all tables if they don't exist.

    Uses SQLAlchemy's create_all for the initial setup.
    Future schema changes should go through Alembic migrations.
    """
    from app.storage.models import Base
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables initialized")
