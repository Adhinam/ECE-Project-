"""FastAPI application — production lifecycle, modular routers, OpenAPI."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import close_database, init_database
from app.dependencies import get_settings
from app.exceptions import register_exception_handlers
from app.middleware import RequestContextMiddleware
from app.routers import analytics, events, health, metrics, stores
from app.settings import Settings
from app.state import AppState
from shared.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """
    Application startup and shutdown.

    Startup:
      - Configure structured logging
      - Connect to PostgreSQL with retries
      - Mark runtime state degraded if DB unavailable

    Shutdown:
      - Dispose SQLAlchemy engine
    """
    settings: Settings = app.state.settings
    runtime: AppState = app.state.runtime

    configure_logging(json_logs=settings.log_json, log_level=settings.log_level)
    logger.info("application_starting", env=settings.app_env)

    db_ok = await init_database()
    if db_ok:
        runtime.mark_db_up()
        logger.info("application_ready", status="ok")
    else:
        runtime.mark_db_down("postgres")
        logger.warning(
            "application_degraded",
            status=runtime.status,
            degraded_features=runtime.degraded_features,
        )

    yield

    await close_database()
    logger.info("application_stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory for tests and uvicorn."""
    settings = settings or get_settings()
    runtime = AppState()

    app = FastAPI(
        title=settings.api_title,
        version=settings.api_version,
        description=settings.api_description,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.state.settings = settings
    app.state.runtime = runtime
    # Kept for backward compatibility; prefer runtime.pipeline_last_event_at
    app.state.pipeline_last_event_at = None

    register_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(stores.router)
    app.include_router(events.router)
    app.include_router(events.router, prefix="/v1")
    app.include_router(metrics.router, prefix="/v1")
    app.include_router(analytics.router, prefix="/v1")

    return app


app = create_app()
