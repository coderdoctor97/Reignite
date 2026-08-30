"""
Central configuration for the Gateway Control Center backend.

All settings are loaded from environment variables with sensible defaults.
Never put real credentials in .env.example — only placeholders.
"""

from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings


# Resolve project root (two levels up from this file: backend/app/core/)
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_PROJECT_ROOT = _BACKEND_DIR.parent


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # ── Application ──────────────────────────────────────────────
    app_name: str = "Gateway Control Center"
    app_version: str = "0.1.0"
    debug: bool = False

    # ── Backend server ───────────────────────────────────────────
    backend_host: str = "127.0.0.1"
    backend_port: int = 8400

    # ── Frontend ─────────────────────────────────────────────────
    frontend_url: str = "http://localhost:5173"

    # ── Database ─────────────────────────────────────────────────
    database_path: str = str(_PROJECT_ROOT / "data" / "gateway.db")

    # ── Logging ──────────────────────────────────────────────────
    log_level: str = "INFO"

    # ── CORS ─────────────────────────────────────────────────────
    cors_origins: str = "http://localhost:5173,http://localhost:4173"

    # ── Legacy compatibility ─────────────────────────────────────
    legacy_base_dir: str = str(_PROJECT_ROOT / "legacy")

    model_config = {
        "env_prefix": "GCC_",
        "env_file": str(_PROJECT_ROOT / ".env"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse CORS_ORIGINS into a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def database_url(self) -> str:
        """SQLite URL for aiosqlite."""
        return f"sqlite+aiosqlite:///{self.database_path}"

    def ensure_data_dir(self) -> None:
        """Create the data directory if it doesn't exist."""
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return the cached singleton Settings instance."""
    return Settings()
