"""Health and readiness endpoints — DEPRECATED: use app.routers.health."""

from __future__ import annotations

import warnings

from fastapi import APIRouter, Depends

from app.dependencies import get_app_state
from app.health import HealthChecker
from app.state import AppState
from schemas.api import HealthResponse

router = APIRouter(tags=["health-legacy"])


@router.get("/health/legacy", response_model=HealthResponse, deprecated=True)
async def health_legacy(state: AppState = Depends(get_app_state)) -> HealthResponse:
    """
    Legacy stub — incomplete health payload.

    The canonical endpoint is GET /health (app.routers.health).
    """
    warnings.warn(
        "api.adapters.http.routes.health is deprecated; mount app.main instead",
        DeprecationWarning,
        stacklevel=2,
    )
    checker = HealthChecker(None, state)
    return await checker.check()
