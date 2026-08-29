"""FastAPI dependency injection — settings, DB, services, auth."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.services.analytics import AnalyticsService
from app.services.ingestion import IngestionService
from app.services.metrics import MetricsService
from app.settings import Settings
from app.state import AppState


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_app_state(request: Request) -> AppState:
    return request.app.state.runtime


async def get_db(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> AsyncGenerator[AsyncSession, None]:
    """Per-request DB session (alias for get_db_session)."""
    yield session


async def verify_api_key(
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


def get_ingestion_service(
    session: Annotated[AsyncSession, Depends(get_db)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> IngestionService:
    return IngestionService(session, state)


def get_metrics_service(
    session: Annotated[AsyncSession, Depends(get_db)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> MetricsService:
    return MetricsService(session, state)


def get_analytics_service(
    session: Annotated[AsyncSession, Depends(get_db)],
    state: Annotated[AppState, Depends(get_app_state)],
) -> AnalyticsService:
    return AnalyticsService(session, state)
