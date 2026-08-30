"""
Repository layer — data access abstractions for the Gateway Control Center.

Each repository provides CRUD operations for a single entity type.
Repositories do NOT contain business logic — they only handle persistence.

All repositories operate on SQLAlchemy models and return domain-friendly
dictionaries or model instances. Services call repositories; repositories
never call services.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import select, update, delete, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.storage.models import (
    ProviderRow,
    ModelRow,
    CredentialRow,
    SessionRow,
    RotationEventRow,
    UsageSnapshotRow,
    SettingRow,
    EventRow,
    HealthCheckRow,
)
from app.storage.database import get_async_session


# ─────────────────────────────────────────────────────────────────
# Provider Repository
# ─────────────────────────────────────────────────────────────────

class ProviderRepository:
    """CRUD for providers."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        name: str,
        protocol: str,
        base_url: str,
        auth_type: str = "api-key",
        provider_id: Optional[str] = None,
    ) -> ProviderRow:
        row = ProviderRow(
            name=name,
            protocol=protocol,
            base_url=base_url,
            auth_type=auth_type,
        )
        if provider_id:
            row.id = provider_id
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_by_id(session: AsyncSession, provider_id: str) -> Optional[ProviderRow]:
        result = await session.execute(
            select(ProviderRow).where(ProviderRow.id == provider_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(session: AsyncSession) -> list[ProviderRow]:
        result = await session.execute(select(ProviderRow).order_by(ProviderRow.name))
        return list(result.scalars().all())

    @staticmethod
    async def update_fields(session: AsyncSession, provider_id: str, **fields) -> bool:
        from app.storage.models import _utcnow
        fields["updated_at"] = _utcnow()
        result = await session.execute(
            update(ProviderRow).where(ProviderRow.id == provider_id).values(**fields)
        )
        return result.rowcount > 0

    @staticmethod
    async def delete_by_id(session: AsyncSession, provider_id: str) -> bool:
        result = await session.execute(
            delete(ProviderRow).where(ProviderRow.id == provider_id)
        )
        return result.rowcount > 0


# ─────────────────────────────────────────────────────────────────
# Model Repository
# ─────────────────────────────────────────────────────────────────

class ModelRepository:
    """CRUD for models."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        provider_id: str,
        display_name: str,
        model_id: str,
        context_window: Optional[int] = None,
        capabilities: Optional[str] = None,
    ) -> ModelRow:
        row = ModelRow(
            provider_id=provider_id,
            display_name=display_name,
            model_id=model_id,
            context_window=context_window,
            capabilities=capabilities,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_by_id(session: AsyncSession, model_id: str) -> Optional[ModelRow]:
        result = await session.execute(
            select(ModelRow).where(ModelRow.id == model_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_provider(session: AsyncSession, provider_id: str) -> list[ModelRow]:
        result = await session.execute(
            select(ModelRow)
            .where(ModelRow.provider_id == provider_id)
            .order_by(ModelRow.display_name)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_all(session: AsyncSession) -> list[ModelRow]:
        result = await session.execute(select(ModelRow).order_by(ModelRow.display_name))
        return list(result.scalars().all())

    @staticmethod
    async def update_fields(session: AsyncSession, model_id: str, **fields) -> bool:
        from app.storage.models import _utcnow
        fields["updated_at"] = _utcnow()
        result = await session.execute(
            update(ModelRow).where(ModelRow.id == model_id).values(**fields)
        )
        return result.rowcount > 0

    @staticmethod
    async def delete_by_id(session: AsyncSession, model_id: str) -> bool:
        result = await session.execute(
            delete(ModelRow).where(ModelRow.id == model_id)
        )
        return result.rowcount > 0


# ─────────────────────────────────────────────────────────────────
# Credential Repository
# ─────────────────────────────────────────────────────────────────

class CredentialRepository:
    """CRUD for credentials.

    Note: actual secret values are NOT stored in the database.
    Only secret_ref (pointing to SecretStore) and key_masked are stored.
    """

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        provider_id: str,
        key_masked: Optional[str] = None,
        secret_ref: Optional[str] = None,
        source: str = "manual",
    ) -> CredentialRow:
        row = CredentialRow(
            provider_id=provider_id,
            key_masked=key_masked,
            secret_ref=secret_ref,
            source=source,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_by_id(session: AsyncSession, credential_id: str) -> Optional[CredentialRow]:
        result = await session.execute(
            select(CredentialRow).where(CredentialRow.id == credential_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_active(session: AsyncSession, provider_id: str) -> Optional[CredentialRow]:
        result = await session.execute(
            select(CredentialRow)
            .where(CredentialRow.provider_id == provider_id)
            .where(CredentialRow.state == "active")
            .order_by(CredentialRow.activated_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_provider(session: AsyncSession, provider_id: str) -> list[CredentialRow]:
        result = await session.execute(
            select(CredentialRow)
            .where(CredentialRow.provider_id == provider_id)
            .order_by(CredentialRow.created_at.desc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_fields(session: AsyncSession, credential_id: str, **fields) -> bool:
        from app.storage.models import _utcnow
        fields["updated_at"] = _utcnow()
        result = await session.execute(
            update(CredentialRow).where(CredentialRow.id == credential_id).values(**fields)
        )
        return result.rowcount > 0

    @staticmethod
    async def deactivate(session: AsyncSession, credential_id: str) -> bool:
        from app.storage.models import _utcnow
        result = await session.execute(
            update(CredentialRow)
            .where(CredentialRow.id == credential_id)
            .values(state="expired", deactivated_at=_utcnow(), updated_at=_utcnow())
        )
        return result.rowcount > 0

    @staticmethod
    async def delete_by_id(session: AsyncSession, credential_id: str) -> bool:
        result = await session.execute(
            delete(CredentialRow).where(CredentialRow.id == credential_id)
        )
        return result.rowcount > 0


# ─────────────────────────────────────────────────────────────────
# Session Repository
# ─────────────────────────────────────────────────────────────────

class SessionRepository:
    """CRUD for sessions.

    Note: actual session values are NOT stored in the database.
    Only secret_ref and session_masked are stored.
    """

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        provider_id: str,
        session_masked: Optional[str] = None,
        secret_ref: Optional[str] = None,
    ) -> SessionRow:
        row = SessionRow(
            provider_id=provider_id,
            session_masked=session_masked,
            secret_ref=secret_ref,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_by_id(session: AsyncSession, session_id: str) -> Optional[SessionRow]:
        result = await session.execute(
            select(SessionRow).where(SessionRow.id == session_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_provider(session: AsyncSession, provider_id: str) -> Optional[SessionRow]:
        result = await session.execute(
            select(SessionRow)
            .where(SessionRow.provider_id == provider_id)
            .order_by(SessionRow.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_all(session: AsyncSession) -> list[SessionRow]:
        result = await session.execute(select(SessionRow).order_by(SessionRow.created_at.desc()))
        return list(result.scalars().all())

    @staticmethod
    async def update_fields(session: AsyncSession, session_id: str, **fields) -> bool:
        from app.storage.models import _utcnow
        fields["updated_at"] = _utcnow()
        result = await session.execute(
            update(SessionRow).where(SessionRow.id == session_id).values(**fields)
        )
        return result.rowcount > 0


# ─────────────────────────────────────────────────────────────────
# Rotation Repository
# ─────────────────────────────────────────────────────────────────

class RotationRepository:
    """CRUD for rotation events."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        trigger_type: str,
        status: str,
        provider_id: Optional[str] = None,
        old_credential_id: Optional[str] = None,
        new_credential_id: Optional[str] = None,
        failure_reason: Optional[str] = None,
        duration_ms: Optional[int] = None,
        details_json: Optional[str] = None,
    ) -> RotationEventRow:
        row = RotationEventRow(
            trigger_type=trigger_type,
            status=status,
            provider_id=provider_id,
            old_credential_id=old_credential_id,
            new_credential_id=new_credential_id,
            failure_reason=failure_reason,
            duration_ms=duration_ms,
            details_json=details_json,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def list_recent(session: AsyncSession, limit: int = 50) -> list[RotationEventRow]:
        result = await session.execute(
            select(RotationEventRow)
            .order_by(RotationEventRow.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_by_provider(session: AsyncSession, provider_id: str, limit: int = 50) -> list[RotationEventRow]:
        result = await session.execute(
            select(RotationEventRow)
            .where(RotationEventRow.provider_id == provider_id)
            .order_by(RotationEventRow.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────
# Usage Repository
# ─────────────────────────────────────────────────────────────────

class UsageRepository:
    """CRUD for usage snapshots."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int = 0,
        remaining: Optional[int] = None,
        limit: Optional[int] = None,
        credential_id: Optional[str] = None,
        provider_id: Optional[str] = None,
    ) -> UsageSnapshotRow:
        row = UsageSnapshotRow(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            remaining=remaining,
            limit=limit,
            credential_id=credential_id,
            provider_id=provider_id,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_latest(session: AsyncSession, credential_id: Optional[str] = None) -> Optional[UsageSnapshotRow]:
        stmt = select(UsageSnapshotRow).order_by(UsageSnapshotRow.snapshot_at.desc()).limit(1)
        if credential_id:
            stmt = stmt.where(UsageSnapshotRow.credential_id == credential_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_recent(session: AsyncSession, limit: int = 100) -> list[UsageSnapshotRow]:
        result = await session.execute(
            select(UsageSnapshotRow)
            .order_by(UsageSnapshotRow.snapshot_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────
# Settings Repository
# ─────────────────────────────────────────────────────────────────

class SettingsRepository:
    """CRUD for application settings."""

    @staticmethod
    async def get(session: AsyncSession, key: str) -> Optional[str]:
        result = await session.execute(
            select(SettingRow.value).where(SettingRow.key == key)
        )
        row = result.scalar_one_or_none()
        return row

    @staticmethod
    async def set(session: AsyncSession, key: str, value: str) -> None:
        from app.storage.models import _utcnow
        existing = await session.execute(
            select(SettingRow).where(SettingRow.key == key)
        )
        row = existing.scalar_one_or_none()
        if row:
            row.value = value
            row.updated_at = _utcnow()
        else:
            session.add(SettingRow(key=key, value=value))
        await session.flush()

    @staticmethod
    async def list_all(session: AsyncSession) -> dict[str, str]:
        result = await session.execute(select(SettingRow))
        return {row.key: row.value for row in result.scalars().all()}

    @staticmethod
    async def delete(session: AsyncSession, key: str) -> bool:
        result = await session.execute(
            delete(SettingRow).where(SettingRow.key == key)
        )
        return result.rowcount > 0


# ─────────────────────────────────────────────────────────────────
# Event Repository
# ─────────────────────────────────────────────────────────────────

class EventRepository:
    """CRUD for structured application events."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        event_type: str,
        message: str,
        severity: str = "info",
        details_json: Optional[str] = None,
    ) -> EventRow:
        row = EventRow(
            event_type=event_type,
            severity=severity,
            message=message,
            details_json=details_json,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def list_recent(session: AsyncSession, limit: int = 100) -> list[EventRow]:
        result = await session.execute(
            select(EventRow).order_by(EventRow.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    @staticmethod
    async def list_by_type(session: AsyncSession, event_type: str, limit: int = 50) -> list[EventRow]:
        result = await session.execute(
            select(EventRow)
            .where(EventRow.event_type == event_type)
            .order_by(EventRow.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────
# Health Repository
# ─────────────────────────────────────────────────────────────────

class HealthRepository:
    """CRUD for health check results."""

    @staticmethod
    async def create(
        session: AsyncSession,
        *,
        target_type: str,
        status: str,
        target_id: Optional[str] = None,
        latency_ms: Optional[float] = None,
        details_json: Optional[str] = None,
    ) -> HealthCheckRow:
        row = HealthCheckRow(
            target_type=target_type,
            target_id=target_id,
            status=status,
            latency_ms=latency_ms,
            details_json=details_json,
        )
        session.add(row)
        await session.flush()
        return row

    @staticmethod
    async def get_latest(session: AsyncSession, target_type: str, target_id: Optional[str] = None) -> Optional[HealthCheckRow]:
        stmt = (
            select(HealthCheckRow)
            .where(HealthCheckRow.target_type == target_type)
            .order_by(HealthCheckRow.checked_at.desc())
            .limit(1)
        )
        if target_id:
            stmt = stmt.where(HealthCheckRow.target_id == target_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_recent(session: AsyncSession, limit: int = 100) -> list[HealthCheckRow]:
        result = await session.execute(
            select(HealthCheckRow).order_by(HealthCheckRow.checked_at.desc()).limit(limit)
        )
        return list(result.scalars().all())
