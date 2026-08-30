"""
Gateway Control Center — FastAPI Application

This is the main entry point for the backend server.
It sets up:
- CORS for the React frontend
- API routes
- Database lifecycle
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

    yield

    # Shutdown
    logger.info("Shutting down...")
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

    return app


# Uvicorn entry point
app = create_app()
