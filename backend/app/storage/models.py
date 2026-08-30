"""
SQLAlchemy models for the Gateway Control Center.

These define the database schema. Alembic generates migrations from them.
Business logic does NOT live here — these are pure persistence models.

Design notes:
- All timestamps use UTC (stored as ISO 8601 strings in SQLite).
- Foreign keys enforce relational integrity.
- Secrets are NOT stored here — only references and masked values.
  Actual secrets live in the SecretStore abstraction.
- JSON columns (metadata, capabilities) store serialized JSON strings.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, relationship


def _utcnow() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Generate a short unique ID (12 hex chars)."""
    return uuid.uuid4().hex[:12]


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""
    pass


# ─────────────────────────────────────────────────────────────────
# Provider
# ─────────────────────────────────────────────────────────────────

class ProviderRow(Base):
    """An upstream API provider (e.g., opus.abhibots.com).

    A provider owns credentials, sessions, and models.
    """
    __tablename__ = "providers"

    id          = Column(String(12), primary_key=True, default=_new_id)
    name        = Column(String(255), nullable=False)
    protocol    = Column(String(64), nullable=False)   # 'openai-completions', 'anthropic-messages'
    base_url    = Column(Text, nullable=False)
    auth_type   = Column(String(32), nullable=False, default="api-key")  # 'api-key', 'session-cookie'
    enabled     = Column(Boolean, nullable=False, default=True)
    health_status = Column(String(32), nullable=False, default="unknown")  # 'healthy','degraded','unhealthy','unknown'
    last_health_check = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)  # arbitrary JSON
    capabilities_json = Column(Text, nullable=True)  # JSON: {"credential_validation":true,"credential_discovery":false,...}
    created_at  = Column(Text, nullable=False, default=_utcnow)
    updated_at  = Column(Text, nullable=False, default=_utcnow, onupdate=_utcnow)

    # Relationships
    credentials = relationship("CredentialRow", back_populates="provider", cascade="all, delete-orphan")
    sessions    = relationship("SessionRow", back_populates="provider", cascade="all, delete-orphan")
    models      = relationship("ModelRow", back_populates="provider", cascade="all, delete-orphan")


# ─────────────────────────────────────────────────────────────────
# Model
# ─────────────────────────────────────────────────────────────────

class ModelRow(Base):
    """A model offered by a provider.

    Supports default and fallback roles for routing.
    """
    __tablename__ = "models"

    id              = Column(String(12), primary_key=True, default=_new_id)
    provider_id     = Column(String(12), ForeignKey("providers.id", ondelete="CASCADE"), nullable=False)
    display_name    = Column(String(255), nullable=False)
    model_id        = Column(String(255), nullable=False)  # upstream model identifier
    context_window  = Column(Integer, nullable=True)
    capabilities    = Column(Text, nullable=True)  # JSON array: ["chat","completion","vision"]
    enabled         = Column(Boolean, nullable=False, default=True)
    is_default      = Column(Boolean, nullable=False, default=False)
    is_fallback     = Column(Boolean, nullable=False, default=False)
    metadata_json   = Column(Text, nullable=True)
    created_at      = Column(Text, nullable=False, default=_utcnow)
    updated_at      = Column(Text, nullable=False, default=_utcnow, onupdate=_utcnow)

    # Relationships
    provider = relationship("ProviderRow", back_populates="models")


# ─────────────────────────────────────────────────────────────────
# Credential
# ─────────────────────────────────────────────────────────────────

class CredentialRow(Base):
    """An API key or bearer token for a provider.

    The actual secret value is NOT stored here. Only:
    - secret_ref: a reference into the SecretStore
    - key_masked: the last 4+ characters for display

    Usage counters are tracked per-credential so they reset when
    a new credential is activated.
    """
    __tablename__ = "credentials"

    id              = Column(String(12), primary_key=True, default=_new_id)
    provider_id     = Column(String(12), ForeignKey("providers.id", ondelete="CASCADE"), nullable=False)
    key_masked      = Column(String(64), nullable=True)   # e.g., "************AB12"
    secret_ref      = Column(String(255), nullable=True)  # reference into SecretStore
    source          = Column(String(32), nullable=False, default="manual")  # 'manual','provider-assisted'
    state           = Column(String(32), nullable=False, default="active")  # 'active','inactive','expired','invalid','revoked'
    validation_status = Column(String(32), nullable=False, default="unknown")  # 'valid','invalid','expired','unknown'
    last_validated  = Column(Text, nullable=True)
    last_validation_error = Column(Text, nullable=True)
    usage_input     = Column(Integer, nullable=False, default=0)
    usage_output    = Column(Integer, nullable=False, default=0)
    usage_total     = Column(Integer, nullable=False, default=0)
    activated_at    = Column(Text, nullable=True)
    deactivated_at  = Column(Text, nullable=True)
    created_at      = Column(Text, nullable=False, default=_utcnow)
    updated_at      = Column(Text, nullable=False, default=_utcnow, onupdate=_utcnow)

    # Relationships
    provider = relationship("ProviderRow", back_populates="credentials")


# ─────────────────────────────────────────────────────────────────
# Session
# ─────────────────────────────────────────────────────────────────

class SessionRow(Base):
    """A session credential for provider dashboard access.

    Used for operations like key management (list/create/delete keys)
    that require a web session cookie or similar auth token.

    The actual session value is NOT stored here — only a reference
    and masked display value.
    """
    __tablename__ = "sessions"

    id              = Column(String(12), primary_key=True, default=_new_id)
    provider_id     = Column(String(12), ForeignKey("providers.id", ondelete="CASCADE"), nullable=False)
    session_masked  = Column(String(128), nullable=True)  # masked for display
    secret_ref      = Column(String(255), nullable=True)  # reference into SecretStore
    status          = Column(String(32), nullable=False, default="unknown")  # 'valid','invalid','expired','unknown'
    last_validated  = Column(Text, nullable=True)
    last_validation_error = Column(Text, nullable=True)
    last_successful_fetch = Column(Text, nullable=True)
    metadata_json   = Column(Text, nullable=True)
    created_at      = Column(Text, nullable=False, default=_utcnow)
    updated_at      = Column(Text, nullable=False, default=_utcnow, onupdate=_utcnow)

    # Relationships
    provider = relationship("ProviderRow", back_populates="sessions")


# ─────────────────────────────────────────────────────────────────
# Credential Event
# ─────────────────────────────────────────────────────────────────

class CredentialEventRow(Base):
    """A record of a credential lifecycle event.

    Event types:
    - 'created': credential record created
    - 'imported_manually': user pasted a credential
    - 'validated': credential validated against provider
    - 'activated': credential set as active
    - 'deactivated': credential deactivated
    - 'expired': credential expired (detected by monitoring)
    - 'invalid': credential rejected by provider
    - 'replacement_requested': user requested replacement
    - 'replacement_completed': new credential activated after replacement
    - 'warning_triggered': monitoring detected a condition requiring attention
    - 'provider_assisted_rotation': provider-specific rotation (where supported)

    Status: 'success', 'failed', 'timeout', 'skipped'
    """
    __tablename__ = "credential_events"

    id                  = Column(String(12), primary_key=True, default=_new_id)
    provider_id         = Column(String(12), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True)
    credential_id       = Column(String(12), ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True)
    event_type          = Column(String(64), nullable=False, index=True)  # see docstring
    status              = Column(String(32), nullable=False)  # 'success','failed','timeout','skipped'
    failure_reason      = Column(Text, nullable=True)
    duration_ms         = Column(Integer, nullable=True)
    details_json        = Column(Text, nullable=True)  # additional context
    created_at          = Column(Text, nullable=False, default=_utcnow)


# ─────────────────────────────────────────────────────────────────
# Usage Snapshot
# ─────────────────────────────────────────────────────────────────

class UsageSnapshotRow(Base):
    """A point-in-time snapshot of token usage.

    Captured periodically or on each request for historical tracking.
    Tied to a specific credential so usage resets on rotation.
    """
    __tablename__ = "usage_snapshots"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    credential_id   = Column(String(12), ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True)
    provider_id     = Column(String(12), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True)
    input_tokens    = Column(Integer, nullable=False, default=0)
    output_tokens   = Column(Integer, nullable=False, default=0)
    total_tokens    = Column(Integer, nullable=False, default=0)
    remaining       = Column(Integer, nullable=True)
    limit           = Column(Integer, nullable=True)
    snapshot_at     = Column(Text, nullable=False, default=_utcnow)


# ─────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────

class SettingRow(Base):
    """Application settings (key-value store).

    Used for: thresholds, behavior flags, UI preferences, etc.
    Not used for secrets — those go in the SecretStore.
    """
    __tablename__ = "settings"

    key         = Column(String(255), primary_key=True)
    value       = Column(Text, nullable=False)
    updated_at  = Column(Text, nullable=False, default=_utcnow)


# ─────────────────────────────────────────────────────────────────
# Event
# ─────────────────────────────────────────────────────────────────

class EventRow(Base):
    """Structured application event log.

    Event types follow the pattern: domain.action
    Examples: gateway.started, rotation.completed, session.failed

    Severity: 'debug', 'info', 'warn', 'error', 'critical'
    """
    __tablename__ = "events"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    event_type  = Column(String(128), nullable=False, index=True)
    severity    = Column(String(16), nullable=False, default="info")
    message     = Column(Text, nullable=False)
    details_json = Column(Text, nullable=True)
    created_at  = Column(Text, nullable=False, default=_utcnow)


# ─────────────────────────────────────────────────────────────────
# Health Check
# ─────────────────────────────────────────────────────────────────

class HealthCheckRow(Base):
    """A health check result for a gateway, provider, or session.

    Target types: 'gateway', 'provider', 'session'
    Status: 'healthy', 'degraded', 'unhealthy'
    """
    __tablename__ = "health_checks"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    target_type = Column(String(32), nullable=False, index=True)  # 'gateway','provider','session'
    target_id   = Column(String(12), nullable=True)  # FK to provider/session, or 'local' for gateway
    status      = Column(String(32), nullable=False)  # 'healthy','degraded','unhealthy'
    latency_ms  = Column(Float, nullable=True)
    details_json = Column(Text, nullable=True)
    checked_at  = Column(Text, nullable=False, default=_utcnow)
