"""
ModelManager — business-logic owner of model configuration and routing.

Models belong to providers and are what the gateway routes requests to.
At most one model per provider can be the default; at most one can be
the fallback. Enabling a model is a manual, explicit action.
"""

from __future__ import annotations

import json
from typing import Optional

from app.core.logging import get_logger
from app.storage.database import get_async_session
from app.storage.models import ModelRow
from app.storage.repositories import ModelRepository, ProviderRepository

logger = get_logger("model_manager")


class ModelManager:
    """Model business logic."""

    async def list_models(self, provider_id: Optional[str] = None) -> list[dict]:
        async with get_async_session() as session:
            if provider_id:
                rows = await ModelRepository.list_by_provider(session, provider_id)
            else:
                rows = await ModelRepository.list_all(session)
            return [self._row_to_dict(r) for r in rows]

    async def get_model(self, model_id: str) -> Optional[dict]:
        async with get_async_session() as session:
            row = await ModelRepository.get_by_id(session, model_id)
            if row is None:
                return None
            return self._row_to_dict(row)

    async def create_model(
        self,
        provider_id: str,
        display_name: str,
        model_id: str,
        context_window: Optional[int] = None,
        capabilities: Optional[list] = None,
    ) -> dict:
        if not display_name.strip():
            raise ValueError("Model display name cannot be empty")
        if not model_id.strip():
            raise ValueError("Model ID cannot be empty")

        async with get_async_session() as session:
            provider = await ProviderRepository.get_by_id(session, provider_id)
            if provider is None:
                raise ValueError(f"Provider not found: {provider_id}")
            row = await ModelRepository.create(
                session,
                provider_id=provider_id,
                display_name=display_name.strip(),
                model_id=model_id.strip(),
                context_window=context_window,
                capabilities=json.dumps(capabilities) if capabilities else None,
            )
            await session.commit()
            logger.info("Model created: id=%s provider=%s model=%s", row.id, provider_id, row.model_id)
            return await self.get_model(row.id)

    async def update_model(self, model_row_id: str, **fields) -> Optional[dict]:
        async with get_async_session() as session:
            row = await ModelRepository.get_by_id(session, model_row_id)
            if row is None:
                return None
            updates = {}
            for key in ("display_name", "model_id", "context_window", "enabled", "metadata_json"):
                if key in fields and fields[key] is not None:
                    updates[key] = fields[key]
            if "capabilities" in fields:
                updates["capabilities"] = json.dumps(fields["capabilities"]) if fields["capabilities"] else None

            if "is_default" in fields and fields["is_default"]:
                # Clear any other default for this provider
                others = await ModelRepository.list_by_provider(session, row.provider_id)
                for other in others:
                    if other.id != model_row_id and other.is_default:
                        await ModelRepository.update_fields(session, other.id, is_default=False)
                updates["is_default"] = True
            if "is_fallback" in fields and fields["is_fallback"]:
                others = await ModelRepository.list_by_provider(session, row.provider_id)
                for other in others:
                    if other.id != model_row_id and other.is_fallback:
                        await ModelRepository.update_fields(session, other.id, is_fallback=False)
                updates["is_fallback"] = True

            await ModelRepository.update_fields(session, model_row_id, **updates)
            await session.commit()
            logger.info("Model updated: id=%s", model_row_id)
            return await self.get_model(model_row_id)

    async def delete_model(self, model_row_id: str) -> bool:
        async with get_async_session() as session:
            row = await ModelRepository.get_by_id(session, model_row_id)
            if row is None:
                return False
            await ModelRepository.delete_by_id(session, model_row_id)
            await session.commit()
            logger.info("Model deleted: id=%s", model_row_id)
            return True

    @staticmethod
    def _parse_capabilities(text: Optional[str]) -> list:
        if not text:
            return []
        try:
            value = json.loads(text)
            return value if isinstance(value, list) else []
        except (ValueError, TypeError):
            return []

    def _row_to_dict(self, row: ModelRow) -> dict:
        return {
            "id": row.id,
            "provider_id": row.provider_id,
            "display_name": row.display_name,
            "model_id": row.model_id,
            "context_window": row.context_window,
            "capabilities": self._parse_capabilities(row.capabilities),
            "enabled": row.enabled,
            "is_default": row.is_default,
            "is_fallback": row.is_fallback,
            "metadata": row.metadata_json,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }


# Module-level singleton
_manager: Optional[ModelManager] = None


def get_model_manager() -> ModelManager:
    global _manager
    if _manager is None:
        _manager = ModelManager()
    return _manager


def set_model_manager(manager: ModelManager) -> None:
    global _manager
    _manager = manager
