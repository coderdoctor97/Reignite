"""
Gateway Control Center — FastAPI Application

This is the main entry point for the backend server.
It sets up:
- CORS for the React frontend
- API routes
- Database lifecycle
- Credential monitor lifecycle
- Logging
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import setup_logging, get_logger
from app.storage.database import init_database, close_database
from app.api.health import router as health_router
from app.api.gateway import router as gateway_router
from app.api.credentials import router as credentials_router
from app.api.monitor import router as monitor_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown hooks."""
    settings = get_settings()
    setup_logging(settings.log_level)
    logger = get_logger("main")

    logger.info("Starting %s v%s", settings.app_name, settings.app_version)
    logger.info("Backend: http://%s:%d", settings.backend_host, settings.backend_port)

    # Initialize database
    await init_database()
    logger.info("Database ready")

    # Start credential monitor if enabled
    monitor = None
    if settings.credential_monitor_enabled:
        try:
            from app.services.credential_monitor import get_credential_monitor
            monitor = get_credential_monitor()
            await monitor.start()
        except Exception as e:
            logger.error("Failed to start credential monitor: %s", e)
            # Don't prevent app from starting if monitor fails

    yield

    # Shutdown
    logger.info("Shutting down...")

    # Stop credential monitor
    if monitor is not None:
        try:
            await monitor.stop()
        except Exception as e:
            logger.error("Error stopping credential monitor: %s", e)

    await close_database()
    logger.info("Shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    # CORS — allow the React dev server and production build
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Register API routers
    app.include_router(health_router)
    app.include_router(gateway_router)
    app.include_router(credentials_router)
    app.include_router(monitor_router)

    return app


# Uvicorn entry point
app = create_app()
