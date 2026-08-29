"""Health, readiness, and liveness endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.dependencies import get_app_state, get_settings
from app.health import HealthChecker
from app.state import AppState
from schemas.api import HealthResponse

router = APIRouter(tags=["health"])


async def _optional_db_session():
    """Yield DB session for health checks; None when the engine is unavailable."""
    from app.database import get_session_factory

    try:
        factory = get_session_factory()
        async with factory() as session:
            yield session
    except Exception:
        yield None


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Production service health",
    description=(
        "Structured operational health: database latency, latest event timestamps, "
        "stale feed detection, per-store status, and ingestion lag. "
        "Status is `ok`, `degraded`, or `down`."
    ),
)
async def health(
    request: Request,
    state: AppState = Depends(get_app_state),
    session: AsyncSession | None = Depends(_optional_db_session),
) -> HealthResponse:
    settings = get_settings()
    pipeline_last = state.pipeline_last_event_at or getattr(
        request.app.state,
        "pipeline_last_event_at",
        None,
    )
    checker = HealthChecker(
        session,
        state,
        pipeline_last_event_at=pipeline_last,
        api_version=settings.api_version,
    )
    return await checker.check()


@router.get("/live", summary="Liveness probe")
async def liveness() -> dict[str, str]:
    """Kubernetes-style liveness — process is running."""
    return {"status": "alive"}


@router.get("/ready", summary="Readiness probe")
async def readiness(state: AppState = Depends(get_app_state)) -> dict[str, object]:
    """Readiness — requires database for full readiness."""
    return {
        "status": "ready" if state.db_available else "not_ready",
        "db_available": state.db_available,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
