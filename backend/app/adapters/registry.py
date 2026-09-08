"""
Adapter registry — maps provider capability declarations to adapter
implementations.

Providers declare their capabilities explicitly (capabilities_json):
    {
      "session_validation": "opus-dashboard",   # validator adapter name
      "dashboard_adapter":  "opus-dashboard"    # key-management adapter name
    }

This registry keeps the generic session/provider system free of
provider-specific logic: adapters are opt-in via declaration only.
Unknown/absent adapter names fall back to safe defaults.
"""

from __future__ import annotations

from typing import Optional

from app.core.logging import get_logger
from app.services.session_manager import SessionValidator, DefaultSessionValidator

logger = get_logger("adapter_registry")

# name → factory(provider) -> SessionValidator
_SESSION_VALIDATORS: dict[str, callable] = {}
# name → factory(provider) -> dashboard adapter with list/create/delete keys
_DASHBOARD_ADAPTERS: dict[str, callable] = {}


def register_session_validator(name: str, factory) -> None:
    _SESSION_VALIDATORS[name] = factory


def register_dashboard_adapter(name: str, factory) -> None:
    _DASHBOARD_ADAPTERS[name] = factory


def _load_builtins() -> None:
    """Register the built-in adapters (idempotent)."""
    if "opus-dashboard" in _SESSION_VALIDATORS:
        return
    from app.adapters.opus_dashboard import (
        OpusDashboardAdapter,
        make_opus_dashboard_validator,
    )
    register_session_validator("opus-dashboard", make_opus_dashboard_validator)
    register_dashboard_adapter("opus-dashboard", OpusDashboardAdapter)


def get_session_validator(provider) -> SessionValidator:
    """Return a SessionValidator for the provider based on its declaration.

    Falls back to the DefaultSessionValidator (honest 'unknown') when the
    provider declares no adapter or an unknown one.
    """
    _load_builtins()
    try:
        import json
        capabilities = json.loads(provider.capabilities_json or "{}")
    except (ValueError, TypeError):
        capabilities = {}
    name = capabilities.get("session_validation")
    factory = _SESSION_VALIDATORS.get(name) if name else None
    if factory is None:
        if name:
            logger.warning(
                "Provider %s declares unknown session_validation adapter '%s' — using default",
                provider.id, name,
            )
        return DefaultSessionValidator()
    return factory(provider)


def get_dashboard_adapter(provider):
    """Return the dashboard management adapter for the provider, or None.

    Returns None when the provider declares no dashboard adapter — the
    caller must then treat provider key workflows as unsupported.
    """
    _load_builtins()
    try:
        import json
        capabilities = json.loads(provider.capabilities_json or "{}")
    except (ValueError, TypeError):
        capabilities = {}
    name = capabilities.get("dashboard_adapter")
    factory = _DASHBOARD_ADAPTERS.get(name) if name else None
    if factory is None:
        if name:
            logger.warning(
                "Provider %s declares unknown dashboard_adapter '%s'", provider.id, name,
            )
        return None
    return factory(provider)
