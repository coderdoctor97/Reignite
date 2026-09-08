"""
Provider key workflow routes — USER-INITIATED provider-specific credential
operations (list / create / revoke keys, import the latest key).

Every route here requires:
1. The provider declares the matching capability
2. An active session for the provider (when the adapter requires one)

Nothing is automatic — no polling, no background rotation.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.provider_workflows import get_provider_workflow_service

router = APIRouter(prefix="/api/providers/{provider_id}/keys", tags=["provider-workflows"])


class CreateKeyRequest(BaseModel):
    name: str = Field(..., min_length=1)
    daily_limit: int = Field(default=1500000, gt=0)


class RevokeResponse(BaseModel):
    revoked: bool
    key_id: str


@router.get("")
async def list_keys(provider_id: str):
    """List keys on the provider's dashboard (masked values only)."""
    service = get_provider_workflow_service()
    try:
        keys = await service.list_keys(provider_id)
        return {"keys": keys, "total": len(keys)}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("")
async def create_key(provider_id: str, request: CreateKeyRequest):
    """Create a key on the provider and store it as a provider-assisted
    credential (inactive — the user validates and activates it)."""
    service = get_provider_workflow_service()
    try:
        result = await service.create_key(
            provider_id, name=request.name, daily_limit=request.daily_limit)
        return result
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.delete("/{key_id}", response_model=RevokeResponse)
async def revoke_key(provider_id: str, key_id: str):
    """Revoke a key on the provider's dashboard."""
    service = get_provider_workflow_service()
    try:
        return RevokeResponse(**await service.revoke_key(provider_id, key_id))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/import-latest")
async def import_latest_key(provider_id: str):
    """Fetch the newest dashboard key; if it differs from the active
    credential, import + validate + activate it (explicit user action)."""
    service = get_provider_workflow_service()
    try:
        return await service.import_latest_key(provider_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
