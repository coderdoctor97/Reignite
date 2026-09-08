"""
Provider API routes — CRUD, health checks, capability declarations,
model discovery, and the OpenRouter quick-add template.

All provider operations are configuration only; nothing here acts on the
provider's systems except explicit user-initiated actions (health probe,
model discovery, key workflows under /api/providers/{id}/keys).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.provider_manager import get_provider_manager

router = APIRouter(prefix="/api/providers", tags=["providers"])


class ProviderCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    protocol: str = Field(default="openai-completions")
    base_url: str = Field(..., min_length=1)
    auth_type: str = Field(default="api-key")
    capabilities: Optional[dict] = None
    metadata: Optional[dict] = None


class ProviderUpdateRequest(BaseModel):
    name: Optional[str] = None
    protocol: Optional[str] = None
    base_url: Optional[str] = None
    auth_type: Optional[str] = None
    enabled: Optional[bool] = None
    capabilities: Optional[dict] = None
    metadata: Optional[dict] = None


class ProviderResponse(BaseModel):
    id: str
    name: str
    protocol: str
    base_url: str
    auth_type: str
    enabled: bool
    health_status: str
    last_health_check: Optional[str] = None
    capabilities: dict = {}
    metadata: Optional[dict] = None
    created_at: str
    updated_at: str


class ProviderListResponse(BaseModel):
    providers: list[ProviderResponse]
    total: int


class ProviderHealthResponse(BaseModel):
    provider_id: str
    status: str
    latency_ms: Optional[float] = None
    http_status: Optional[int] = None
    error: Optional[str] = None


class ModelDiscoveryResponse(BaseModel):
    imported: int
    skipped: int
    models: list[str]


@router.get("", response_model=ProviderListResponse)
async def list_providers():
    manager = get_provider_manager()
    providers = await manager.list_providers()
    return ProviderListResponse(providers=[ProviderResponse(**p) for p in providers], total=len(providers))


@router.post("", response_model=ProviderResponse, status_code=201)
async def create_provider(request: ProviderCreateRequest):
    manager = get_provider_manager()
    try:
        provider = await manager.create_provider(
            name=request.name,
            protocol=request.protocol,
            base_url=request.base_url,
            auth_type=request.auth_type,
            capabilities=request.capabilities,
            metadata=request.metadata,
        )
        return ProviderResponse(**provider)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/openrouter", response_model=ProviderResponse, status_code=201)
async def add_openrouter(name: str = "OpenRouter"):
    """Quick-add an OpenRouter provider with the standard template."""
    manager = get_provider_manager()
    return ProviderResponse(**await manager.create_openrouter_provider(name=name))


@router.get("/{provider_id}", response_model=ProviderResponse)
async def get_provider(provider_id: str):
    manager = get_provider_manager()
    provider = await manager.get_provider(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return ProviderResponse(**provider)


@router.put("/{provider_id}", response_model=ProviderResponse)
async def update_provider(provider_id: str, request: ProviderUpdateRequest):
    manager = get_provider_manager()
    try:
        provider = await manager.update_provider(provider_id, **request.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return ProviderResponse(**provider)


@router.delete("/{provider_id}")
async def delete_provider(provider_id: str):
    manager = get_provider_manager()
    deleted = await manager.delete_provider(provider_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider not found")
    return {"success": True, "message": "Provider deleted (cascaded to its models, credentials, sessions)"}


@router.post("/{provider_id}/check", response_model=ProviderHealthResponse)
async def check_provider(provider_id: str):
    """Probe provider reachability (generic HTTP check)."""
    manager = get_provider_manager()
    try:
        result = await manager.check_health(provider_id)
        return ProviderHealthResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/{provider_id}/discover-models", response_model=ModelDiscoveryResponse)
async def discover_models(provider_id: str):
    """Import models from an OpenAI-compatible GET /models endpoint."""
    manager = get_provider_manager()
    try:
        result = await manager.discover_models(provider_id)
        return ModelDiscoveryResponse(**result)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
