"""Store-level routes."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_analytics_service, get_metrics_service
from app.services.analytics import AnalyticsService
from app.services.metrics import MetricsService
from schemas.api import AnomaliesResponse, FunnelResponse, HeatmapResponse, StoreMetricsResponse

router = APIRouter(prefix="/stores", tags=["stores"])


@router.get(
    "/{store_id}/metrics",
    response_model=StoreMetricsResponse,
    summary="Real-time store metrics",
    description=(
        "Session-aware KPIs for a store: unique visitors, conversion, zone dwell, "
        "queue depth, and abandonment. Staff events are excluded. Empty stores return "
        "zeroed metrics with `is_empty=true`."
    ),
)
async def store_metrics(
    store_id: str,
    window_minutes: int = Query(15, ge=1, le=1440, description="Rolling window in minutes"),
    checkout_zone_id: str = Query(
        "checkout",
        description="Zone ID used as purchase/checkout proxy for conversion rate",
    ),
    service: MetricsService = Depends(get_metrics_service),
) -> StoreMetricsResponse:
    return await service.get_store_metrics(
        store_id,
        window_minutes=window_minutes,
        checkout_zone_id=checkout_zone_id,
    )


@router.get(
    "/{store_id}/funnel",
    response_model=FunnelResponse,
    summary="Session-based store funnel",
    description=(
        "Funnel stages: ENTRY → ZONE_VISIT → BILLING_QUEUE → PURCHASE. "
        "Re-entries within the configured window merge into one session. "
        "Staff are excluded. Empty windows return zeroed stages with `is_empty=true`."
    ),
)
async def store_funnel(
    store_id: str,
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    window_minutes: int = Query(60, ge=1, le=10080),
    checkout_zone_id: str = Query("checkout"),
    billing_queue_id: str = Query("checkout-1"),
    reentry_window_seconds: int = Query(300, ge=0, le=86400),
    service: AnalyticsService = Depends(get_analytics_service),
) -> FunnelResponse:
    now = datetime.now(timezone.utc)
    end = to_time or now
    start = from_time or (end - timedelta(minutes=window_minutes))
    return await service.get_funnel(
        store_id,
        start,
        end,
        checkout_zone_id=checkout_zone_id,
        billing_queue_id=billing_queue_id,
        reentry_window_seconds=float(reentry_window_seconds),
    )


@router.get(
    "/{store_id}/heatmap",
    response_model=HeatmapResponse,
    summary="Store heatmap",
    description=(
        "Spatial visit density from position_snapshot and zone events. "
        "Cells include visit counts and accumulated dwell seconds."
    ),
)
async def store_heatmap(
    store_id: str,
    from_time: datetime | None = Query(None, alias="from"),
    to_time: datetime | None = Query(None, alias="to"),
    window_minutes: int = Query(60, ge=1, le=10080),
    resolution: int = Query(32, ge=8, le=128),
    service: AnalyticsService = Depends(get_analytics_service),
) -> HeatmapResponse:
    now = datetime.now(timezone.utc)
    end = to_time or now
    start = from_time or (end - timedelta(minutes=window_minutes))
    return await service.get_heatmap(store_id, start, end, resolution)


@router.get(
    "/{store_id}/anomalies",
    response_model=AnomaliesResponse,
    summary="Live store anomalies",
    description=(
        "Detects queue spikes, conversion drops, dead zones, and stale feeds using "
        "rolling baselines. Severity: INFO, WARN, CRITICAL with suggested actions."
    ),
)
async def store_anomalies(
    store_id: str,
    limit: int = Query(20, ge=1, le=100),
    checkout_zone_id: str = Query("checkout"),
    billing_queue_id: str = Query("checkout-1"),
    service: AnalyticsService = Depends(get_analytics_service),
) -> AnomaliesResponse:
    return await service.get_anomalies(
        store_id,
        limit=limit,
        live=True,
        checkout_zone_id=checkout_zone_id,
        billing_queue_id=billing_queue_id,
    )
