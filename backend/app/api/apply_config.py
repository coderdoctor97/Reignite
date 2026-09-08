"""
Apply-Config API routes — point installed apps and CLI agents at the
gateway endpoint.

GET  /api/apply-config            — status of all targets + master toggle
PUT  /api/apply-config/master     — master toggle
PUT  /api/apply-config/targets/{id} — per-target toggle
POST /api/apply-config/apply      — write configs now (enabled targets)
POST /api/apply-config/revert     — restore previous configs
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.config_applier import get_config_applier, TARGETS

router = APIRouter(prefix="/api/apply-config", tags=["apply-config"])


class MasterToggleRequest(BaseModel):
    enabled: bool


class TargetToggleRequest(BaseModel):
    enabled: bool


class ApplyRequest(BaseModel):
    target_ids: Optional[list[str]] = None


@router.get("")
async def apply_config_status():
    """Status of every target (installed / applied / enabled)."""
    applier = get_config_applier()
    return await applier.get_status()


@router.put("/master")
async def set_master(request: MasterToggleRequest):
    applier = get_config_applier()
    return {"master_enabled": await applier.set_master_enabled(request.enabled)}


@router.put("/targets/{target_id}")
async def set_target_toggle(target_id: str, request: TargetToggleRequest):
    if target_id not in TARGETS:
        raise HTTPException(status_code=404, detail=f"Unknown target: {target_id}")
    applier = get_config_applier()
    return {"target": target_id, "enabled": await applier.set_target_enabled(target_id, request.enabled)}


@router.post("/apply")
async def apply_configs(request: Optional[ApplyRequest] = None):
    """Write the gateway config to the selected (or enabled) targets."""
    applier = get_config_applier()
    target_ids = request.target_ids if request else None
    return await applier.apply(target_ids=target_ids)


@router.post("/revert")
async def revert_configs(request: Optional[ApplyRequest] = None):
    """Restore previous configs for the selected (or all) targets."""
    applier = get_config_applier()
    target_ids = request.target_ids if request else None
    return await applier.revert(target_ids=target_ids)
