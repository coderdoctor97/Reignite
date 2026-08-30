"""
Credential API routes — manual entry, validation, activation, replacement.

Endpoints:
    GET  /api/credentials                    — list all credentials
    GET  /api/credentials/active             — get the active credential
    GET  /api/credentials/{credential_id}    — get a single credential
    POST /api/credentials                    — add a new credential
    POST /api/credentials/{credential_id}/validate   — validate a credential
    POST /api/credentials/{credential_id}/activate    — activate a credential
    POST /api/credentials/{credential_id}/deactivate  — deactivate a credential
    POST /api/credentials/replace            — replace the active credential
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.services.credential_manager import get_credential_manager

router = APIRouter(prefix="/api/credentials", tags=["credentials"])


# ── Request models ───────────────────────────────────────────────

class AddCredentialRequest(BaseModel):
    """Request to add a new credential."""
    credential_value: str = Field(..., min_length=1, description="The raw API key or token")
    provider_id: str = Field(..., min_length=1, description="Provider this credential belongs to")
    source: str = Field(default="manual", description="How the credential was obtained")


class ReplaceCredentialRequest(BaseModel):
    """Request to replace the active credential."""
    credential_value: str = Field(..., min_length=1, description="The new API key or token")
    provider_id: str = Field(..., min_length=1, description="Provider this credential belongs to")


# ── Response models ──────────────────────────────────────────────

class CredentialResponse(BaseModel):
    """Safe credential metadata — no raw secrets."""
    id: str
    provider_id: str
    key_masked: Optional[str] = None
    source: str
    state: str
    validation_status: str
    last_validated: Optional[str] = None
    last_validation_error: Optional[str] = None
    usage_input: int = 0
    usage_output: int = 0
    usage_total: int = 0
    activated_at: Optional[str] = None
    deactivated_at: Optional[str] = None
    created_at: str
    updated_at: str


class CredentialListResponse(BaseModel):
    """List of credentials."""
    credentials: list[CredentialResponse]
    total: int


class CredentialActionResponse(BaseModel):
    """Response for credential actions (activate, deactivate, validate)."""
    success: bool
    message: str
    credential: CredentialResponse


# ── Routes ───────────────────────────────────────────────────────

@router.get("", response_model=CredentialListResponse)
async def list_credentials(provider_id: Optional[str] = None):
    """List all credentials. Returns safe metadata only — no raw secrets."""
    manager = get_credential_manager()
    creds = await manager.list_credentials(provider_id=provider_id)
    return CredentialListResponse(
        credentials=[CredentialResponse(**c) for c in creds],
        total=len(creds),
    )


@router.get("/active", response_model=Optional[CredentialResponse])
async def get_active_credential(provider_id: Optional[str] = None):
    """Get the currently active credential for a provider."""
    manager = get_credential_manager()
    cred = await manager.get_active_credential(provider_id=provider_id)
    if cred is None:
        return None
    return CredentialResponse(**cred)


@router.get("/{credential_id}", response_model=CredentialResponse)
async def get_credential(credential_id: str):
    """Get a single credential by ID. Returns safe metadata only."""
    manager = get_credential_manager()
    cred = await manager.get_credential(credential_id)
    if cred is None:
        raise HTTPException(status_code=404, detail="Credential not found")
    return CredentialResponse(**cred)


@router.post("", response_model=CredentialResponse, status_code=201)
async def add_credential(request: AddCredentialRequest):
    """Add a new credential.

    The credential value is stored securely via the SecretStore.
    Only metadata and a masked representation are returned.
    The raw value is never returned in responses.
    """
    manager = get_credential_manager()
    try:
        cred = await manager.add_credential(
            credential_value=request.credential_value,
            provider_id=request.provider_id,
            source=request.source,
        )
        return CredentialResponse(**cred)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{credential_id}/validate", response_model=CredentialActionResponse)
async def validate_credential(credential_id: str):
    """Validate a credential.

    Checks if the credential exists in the secret store.
    Provider-specific validation will be added in future phases.
    """
    manager = get_credential_manager()
    try:
        cred = await manager.validate_credential(credential_id)
        status = cred["validation_status"]
        return CredentialActionResponse(
            success=True,
            message=f"Validation status: {status}",
            credential=CredentialResponse(**cred),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{credential_id}/activate", response_model=CredentialActionResponse)
async def activate_credential(credential_id: str):
    """Activate a credential.

    Deactivates any currently active credential for the same provider,
    then activates this one. The legacy gateway will discover the change
    on its next reload cycle.
    """
    manager = get_credential_manager()
    try:
        cred = await manager.activate_credential(credential_id)
        return CredentialActionResponse(
            success=True,
            message="Credential activated",
            credential=CredentialResponse(**cred),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{credential_id}/deactivate", response_model=CredentialActionResponse)
async def deactivate_credential(credential_id: str):
    """Deactivate a credential.

    Marks the credential as inactive and clears the active key file
    if this was the active credential.
    """
    manager = get_credential_manager()
    try:
        cred = await manager.deactivate_credential(credential_id)
        return CredentialActionResponse(
            success=True,
            message="Credential deactivated",
            credential=CredentialResponse(**cred),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/replace", response_model=CredentialActionResponse)
async def replace_credential(request: ReplaceCredentialRequest):
    """Replace the active credential with a new one.

    This is the explicit user-initiated replacement workflow:
    1. Add the new credential
    2. Deactivate the current active credential
    3. Activate the new credential
    4. Record replacement events

    The previous credential is deactivated, not deleted.
    """
    manager = get_credential_manager()
    try:
        cred = await manager.replace_credential(
            new_credential_value=request.credential_value,
            provider_id=request.provider_id,
        )
        return CredentialActionResponse(
            success=True,
            message="Credential replaced",
            credential=CredentialResponse(**cred),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
