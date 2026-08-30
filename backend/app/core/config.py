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

    # ── Gateway process ──────────────────────────────────────────
    gateway_script: str = "OpusGateway.py"
    gateway_host: str = "127.0.0.1"
    gateway_port: int = 5800
    gateway_protocol: str = "http"
    gateway_base_path: str = "/v1"
    gateway_startup_timeout: float = 10.0
    gateway_shutdown_timeout: float = 5.0

    # ── Credential health monitoring ─────────────────────────────
    credential_validation_interval: float = 3600.0  # seconds between validation checks (default: 1 hour)
    credential_validation_enabled: bool = True

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
    def gateway_endpoint_url(self) -> str:
        """Full URL for the local gateway endpoint (e.g. http://127.0.0.1:5800/v1)."""
        return f"{self.gateway_protocol}://{self.gateway_host}:{self.gateway_port}{self.gateway_base_path}"

    @property
    def gateway_base_url(self) -> str:
        """Base URL without the path (e.g. http://127.0.0.1:5800)."""
        return f"{self.gateway_protocol}://{self.gateway_host}:{self.gateway_port}"

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
