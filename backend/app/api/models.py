"""
Model API routes — model CRUD, default/fallback routing configuration.

Models are what the gateway routes requests to. Enabling models and
setting defaults/fallbacks are always manual, explicit actions.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.model_manager import get_model_manager

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelCreateRequest(BaseModel):
    provider_id: str = Field(..., min_length=1)
    display_name: str = Field(..., min_length=1)
    model_id: str = Field(..., min_length=1)
    context_window: Optional[int] = None
    capabilities: Optional[list] = None


class ModelUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    model_id: Optional[str] = None
    context_window: Optional[int] = None
    capabilities: Optional[list] = None
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    is_fallback: Optional[bool] = None


class ModelResponse(BaseModel):
    id: str
    provider_id: str
    display_name: str
    model_id: str
    context_window: Optional[int] = None
    capabilities: list = []
    enabled: bool
    is_default: bool
    is_fallback: bool
    metadata: Optional[str] = None
    created_at: str
    updated_at: str


class ModelListResponse(BaseModel):
    models: list[ModelResponse]
    total: int


@router.get("", response_model=ModelListResponse)
async def list_models(provider_id: Optional[str] = None):
    manager = get_model_manager()
    models = await manager.list_models(provider_id=provider_id)
    return ModelListResponse(models=[ModelResponse(**m) for m in models], total=len(models))


@router.post("", response_model=ModelResponse, status_code=201)
async def create_model(request: ModelCreateRequest):
    manager = get_model_manager()
    try:
        model = await manager.create_model(
            provider_id=request.provider_id,
            display_name=request.display_name,
            model_id=request.model_id,
            context_window=request.context_window,
            capabilities=request.capabilities,
        )
        return ModelResponse(**model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{model_row_id}", response_model=ModelResponse)
async def get_model(model_row_id: str):
    manager = get_model_manager()
    model = await manager.get_model(model_row_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return ModelResponse(**model)


@router.put("/{model_row_id}", response_model=ModelResponse)
async def update_model(model_row_id: str, request: ModelUpdateRequest):
    manager = get_model_manager()
    model = await manager.update_model(model_row_id, **request.model_dump(exclude_none=True))
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return ModelResponse(**model)


@router.delete("/{model_row_id}")
async def delete_model(model_row_id: str):
    manager = get_model_manager()
    deleted = await manager.delete_model(model_row_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Model not found")
    return {"success": True, "message": "Model deleted"}
