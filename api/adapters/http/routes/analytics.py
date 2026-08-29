"""Analytics routes — funnel, heatmap, queue, anomalies."""

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from api.dependencies import get_analytics_service
from api.services.analytics_service import AnalyticsService
from schemas.api import (
    AnomaliesResponse,
    FunnelResponse,
    HeatmapResponse,
    QueueAnalyticsResponse,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/funnel", response_model=FunnelResponse)
async def funnel(
    store_id: str = Query(...),
    from_time: datetime = Query(..., alias="from"),
    to_time: datetime = Query(..., alias="to"),
    service: AnalyticsService = Depends(get_analytics_service),
) -> FunnelResponse:
    return await service.get_funnel(store_id, from_time, to_time)


@router.get("/heatmap", response_model=HeatmapResponse)
async def heatmap(
    store_id: str = Query(...),
    from_time: datetime = Query(..., alias="from"),
    to_time: datetime = Query(..., alias="to"),
    resolution: int = Query(32, ge=8, le=128),
    service: AnalyticsService = Depends(get_analytics_service),
) -> HeatmapResponse:
    return await service.get_heatmap(store_id, from_time, to_time, resolution)


@router.get("/queue", response_model=QueueAnalyticsResponse)
async def queue_analytics(
    store_id: str = Query(...),
    queue_id: str = Query(...),
    window_minutes: int = Query(60, ge=1),
    service: AnalyticsService = Depends(get_analytics_service),
) -> QueueAnalyticsResponse:
    return await service.get_queue(store_id, queue_id, window_minutes)


@router.get("/anomalies", response_model=AnomaliesResponse)
async def anomalies(
    store_id: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    service: AnalyticsService = Depends(get_analytics_service),
) -> AnomaliesResponse:
    return await service.get_anomalies(store_id, limit)
