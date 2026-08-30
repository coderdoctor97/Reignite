"""
Base service class for the Gateway Control Center.

All services inherit from this base to ensure consistent
logging, error handling, and lifecycle management.
"""

from __future__ import annotations

from app.core.logging import get_logger


class BaseService:
    """Base class for all backend services.

    Provides:
    - A named logger instance
    - Consistent service naming for logs
    - Placeholder lifecycle hooks for future use
    """

    def __init__(self) -> None:
        self._logger = get_logger(self.__class__.__name__)

    @property
    def logger(self):
        """Return the service's named logger."""
        return self._logger

    async def startup(self) -> None:
        """Called when the application starts. Override in subclasses."""
        pass

    async def shutdown(self) -> None:
        """Called when the application stops. Override in subclasses."""
        pass
