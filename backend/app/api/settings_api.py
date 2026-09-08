"""
Settings API routes — runtime application settings.

Settings live in the settings table with environment-variable defaults.
They never contain secrets. The gateway auth token is the only
credential-like value and is intentionally treated as a local trust
token, not a secret store entry.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.config import get_settings
from app.storage.database import get_async_session
from app.storage.repositories import SettingsRepository

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Runtime-editable settings (setting_key → env fallback getter)
_EDITABLE = {
    "gateway_public_base_url": lambda s: s.gateway_public_base_url,
    "gateway_auth_token": lambda s: s.gateway_auth_token,
    "credential_validation_interval": lambda s: str(s.credential_validation_interval),
    "session_validation_interval": lambda s: str(s.session_validation_interval),
    "usage_limit": lambda s: str(s.usage_limit),
    "usage_warning_threshold": lambda s: str(s.usage_warning_threshold),
}


class SettingsUpdateRequest(BaseModel):
    settings: dict = {}


@router.get("")
async def get_settings():
    """Return current runtime settings with their effective values."""
    env = get_settings()
    async with get_async_session() as session:
        stored = await SettingsRepository.list_all(session)

    result = {}
    for key, fallback in _EDITABLE.items():
        result[key] = stored.get(key, fallback(env))

    # Read-only information
    result["readonly"] = {
        "app_version": env.app_version,
        "backend_port": env.backend_port,
        "gateway_base_path": env.gateway_base_path,
        "database_path": env.database_path,
        "credential_monitor_enabled": env.credential_monitor_enabled,
        "credential_monitor_interval": env.credential_monitor_interval,
        "demo_provider_enabled": env.demo_provider_enabled,
        "demo_provider_url": env.demo_provider_url,
    }
    return result


@router.put("")
async def update_settings(request: SettingsUpdateRequest):
    """Update runtime settings (whitelisted keys only)."""
    unknown = [k for k in request.settings if k not in _EDITABLE]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown settings: {', '.join(unknown)}")

    async with get_async_session() as session:
        for key, value in request.settings.items():
            if value is None:
                await SettingsRepository.delete(session, key)
            else:
                await SettingsRepository.set(session, key, str(value))
        await session.commit()
    return await get_settings()
