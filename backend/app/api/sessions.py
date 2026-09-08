"""
Session API routes — manual entry, validation, activation, replacement, health.

Endpoints:
    GET  /api/sessions                     — list all sessions
    GET  /api/sessions/health              — health summary for all sessions
    GET  /api/sessions/active              — get the active session
    GET  /api/sessions/{session_id}        — get a single session
    POST /api/sessions                     — add a new session manually
    POST /api/sessions/replace             — replace an existing session
    POST /api/sessions/{session_id}/validate   — validate a session
    POST /api/sessions/{session_id}/activate    — activate a session
    POST /api/sessions/{session_id}/deactivate  — deactivate a session

There is deliberately NO DELETE endpoint — deactivation is preferred.
All responses contain safe metadata only; session secrets are never
returned. No automatic session renewal exists in this phase.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app.services.session_manager import (
    get_session_manager,
    SessionLifecycleError,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# ── Request models ───────────────────────────────────────────────

class AddSessionRequest(BaseModel):
    """Request to add a new session manually."""
    secret_value: str = Field(..., min_length=1, description="The raw session secret (cookie value, token, ...)")
    provider_id: str = Field(..., min_length=1, description="Provider this session belongs to")
    label: Optional[str] = Field(default=None, max_length=255, description="Optional user-facing label")
    source: str = Field(default="manual", description="How the session was obtained")


class ReplaceSessionRequest(BaseModel):
    """Request to replace an existing session with a new one."""
    secret_value: str = Field(..., min_length=1, description="The new session secret")
    provider_id: str = Field(..., min_length=1, description="Provider this session belongs to")
    label: Optional[str] = Field(default=None, max_length=255, description="Optional label for the new session")
    session_id: Optional[str] = Field(
        default=None,
        description="Optional explicit target session. Defaults to the active "
                    "session (or the most recent one) for the provider.",
    )


# ── Response models ──────────────────────────────────────────────

class SessionResponse(BaseModel):
    """Safe session metadata — no raw secrets, no secret references."""
    id: str
    provider_id: str
    label: Optional[str] = None
    session_masked: Optional[str] = None
    source: str
    lifecycle_state: str
    validation_state: str
    health: str
    last_validated: Optional[str] = None
    next_validation_at: Optional[str] = None
    last_validation_error: Optional[str] = None
    last_successful_fetch: Optional[str] = None
    activated_at: Optional[str] = None
    deactivated_at: Optional[str] = None
    created_at: str
    updated_at: str


class SessionListResponse(BaseModel):
    """List of sessions."""
    sessions: list[SessionResponse]
    total: int


class SessionActionResponse(BaseModel):
    """Response for session actions (add, validate, activate, deactivate, replace)."""
    success: bool
    message: str
    session: SessionResponse


class SessionHealthResponse(BaseModel):
    """Health summary for a session."""
    session_id: str
    provider_id: str
    session_masked: Optional[str] = None
    lifecycle_state: str
    validation_state: str
    health: str
    last_validated: Optional[str] = None
    next_validation_at: Optional[str] = None
    last_validation_error: Optional[str] = None


class SessionHealthListResponse(BaseModel):
    """Health summaries for all sessions."""
    sessions: list[SessionHealthResponse]
    total: int
    summary: dict  # counts by health state


# ── Routes ───────────────────────────────────────────────────────

@router.get("", response_model=SessionListResponse)
async def list_sessions(provider_id: Optional[str] = None):
    """List all sessions. Returns safe metadata only — no raw secrets."""
    manager = get_session_manager()
    sessions = await manager.list_sessions(provider_id=provider_id)
    return SessionListResponse(
        sessions=[SessionResponse(**s) for s in sessions],
        total=len(sessions),
    )


@router.get("/health", response_model=SessionHealthListResponse)
async def get_all_session_health():
    """Get health summaries for all sessions without running new validations."""
    manager = get_session_manager()
    sessions = await manager.list_sessions()

    summary = {"healthy": 0, "warning": 0, "critical": 0, "unknown": 0}
    health_list = []
    for s in sessions:
        health = s.get("health", "unknown")
        summary[health] = summary.get(health, 0) + 1
        health_list.append({
            "session_id": s["id"],
            "provider_id": s["provider_id"],
            "session_masked": s["session_masked"],
            "lifecycle_state": s["lifecycle_state"],
            "validation_state": s["validation_state"],
            "health": health,
            "last_validated": s["last_validated"],
            "next_validation_at": s["next_validation_at"],
            "last_validation_error": s["last_validation_error"],
        })

    return SessionHealthListResponse(
        sessions=[SessionHealthResponse(**h) for h in health_list],
        total=len(health_list),
        summary=summary,
    )


@router.get("/active", response_model=Optional[SessionResponse])
async def get_active_session(provider_id: Optional[str] = None):
    """Get the currently active session for a provider."""
    manager = get_session_manager()
    session = await manager.get_active_session(provider_id=provider_id)
    if session is None:
        return None
    return SessionResponse(**session)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(session_id: str):
    """Get a single session by ID. Returns safe metadata only."""
    manager = get_session_manager()
    session = await manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionResponse(**session)


@router.post("", response_model=SessionResponse, status_code=201)
async def add_session(request: AddSessionRequest):
    """Add a new session manually.

    The session secret is stored securely via the SecretStore.
    Only metadata and a masked representation are returned.
    The raw secret is never returned in responses or logged.
    New sessions start 'inactive' — activation is explicit.
    """
    manager = get_session_manager()
    try:
        session = await manager.add_session(
            secret_value=request.secret_value,
            provider_id=request.provider_id,
            label=request.label,
            source=request.source,
        )
        return SessionResponse(**session)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/replace", response_model=SessionActionResponse)
async def replace_session(request: ReplaceSessionRequest):
    """Replace an existing session with a new one.

    Explicit user-initiated workflow: the new session is stored, validated,
    and activated; the previous session is deactivated (NOT deleted) and an
    audit event chain is preserved. If validation of the new session fails,
    the previous session is left unchanged.
    """
    manager = get_session_manager()
    try:
        session = await manager.replace_session(
            secret_value=request.secret_value,
            provider_id=request.provider_id,
            label=request.label,
            session_id=request.session_id,
        )
        return SessionActionResponse(
            success=True,
            message="Session replaced",
            session=SessionResponse(**session),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{session_id}/validate", response_model=SessionActionResponse)
async def validate_session(session_id: str):
    """Validate a session.

    Invokes the SessionValidator abstraction, updates validation_state,
    last_validated and next_validation_at, syncs lifecycle state, and
    records events. Without a provider-specific adapter the result is
    honestly 'unknown' — a session is never reported valid merely
    because its secret exists in the SecretStore.
    """
    manager = get_session_manager()
    try:
        session = await manager.validate_session(session_id)
        return SessionActionResponse(
            success=True,
            message=f"Validation state: {session['validation_state']}",
            session=SessionResponse(**session),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{session_id}/activate", response_model=SessionActionResponse)
async def activate_session(session_id: str):
    """Activate a session.

    Deactivates any currently active session for the same provider, then
    activates this one. Activation is refused (409) when the session is
    expired or invalid — it must be replaced first.
    """
    manager = get_session_manager()
    try:
        session = await manager.activate_session(session_id)
        return SessionActionResponse(
            success=True,
            message="Session activated",
            session=SessionResponse(**session),
        )
    except SessionLifecycleError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{session_id}/deactivate", response_model=SessionActionResponse)
async def deactivate_session(session_id: str):
    """Deactivate a session.

    The session is preserved (metadata + stored secret) but no longer in
    service. There is no DELETE endpoint — deactivation is preferred.
    """
    manager = get_session_manager()
    try:
        session = await manager.deactivate_session(session_id)
        return SessionActionResponse(
            success=True,
            message="Session deactivated",
            session=SessionResponse(**session),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
