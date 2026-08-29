"""Real-time metrics routes."""

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_metrics_service
from app.services.metrics import MetricsService
from schemas.api import RealtimeMetricsResponse

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get(
    "/realtime",
    response_model=RealtimeMetricsResponse,
    summary="Real-time store metrics",
)
async def realtime_metrics(
    store_id: str = Query(..., description="Store identifier"),
    window_minutes: int = Query(15, ge=1, le=1440),
    service: MetricsService = Depends(get_metrics_service),
) -> RealtimeMetricsResponse:
    return await service.get_realtime(store_id=store_id, window_minutes=window_minutes)
