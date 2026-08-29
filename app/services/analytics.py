"""Analytics use-case — funnel, heatmap, queue, anomalies."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.anomalies import AnomalyDetectionEngine
from app.funnel import FunnelAnalyticsEngine
from app.heatmap import HeatmapEngine
from app.repositories import AnomalyRepository
from app.state import AppState
from schemas.api import (
    AnomaliesResponse,
    AnomalyItem,
    AnomalySeverity,
    FunnelResponse,
    HeatmapResponse,
    QueueAnalyticsResponse,
)


class AnalyticsService:
    def __init__(self, session: AsyncSession, app_state: AppState) -> None:
        self._session = session
        self._state = app_state
        self._anomalies = AnomalyRepository(session)
        self._funnel = FunnelAnalyticsEngine(session, app_state)
        self._anomaly_engine = AnomalyDetectionEngine(session, app_state)
        self._heatmap = HeatmapEngine(session, app_state)

    async def get_funnel(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
        *,
        checkout_zone_id: str = "checkout",
        billing_queue_id: str = "checkout-1",
        reentry_window_seconds: float = 300.0,
    ) -> FunnelResponse:
        return await self._funnel.get_funnel(
            store_id,
            from_time=from_time,
            to_time=to_time,
            checkout_zone_id=checkout_zone_id,
            billing_queue_id=billing_queue_id,
            reentry_window_seconds=reentry_window_seconds,
        )

    async def get_heatmap(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
        resolution: int,
    ) -> HeatmapResponse:
        return await self._heatmap.get_heatmap(
            store_id,
            from_time,
            to_time,
            resolution,
        )

    async def get_queue(
        self,
        store_id: str,
        queue_id: str,
        window_minutes: int,
    ) -> QueueAnalyticsResponse:
        return QueueAnalyticsResponse(store_id=store_id, queue_id=queue_id)

    async def get_anomalies(
        self,
        store_id: str,
        limit: int = 20,
        *,
        live: bool = True,
        checkout_zone_id: str = "checkout",
        billing_queue_id: str = "checkout-1",
    ) -> AnomaliesResponse:
        if live:
            return await self._anomaly_engine.detect(
                store_id,
                limit=limit,
                checkout_zone_id=checkout_zone_id,
                billing_queue_id=billing_queue_id,
            )

        if not self._state.db_available:
            return AnomaliesResponse(store_id=store_id, items=[])

        rows = await self._anomalies.list_recent(store_id, limit)
        return AnomaliesResponse(
            store_id=store_id,
            items=[_anomaly_row_to_item(r) for r in rows],
        )


def _anomaly_row_to_item(row) -> AnomalyItem:
    details = row.details or {}
    severity_raw = str(row.severity).upper()
    if severity_raw in ("HIGH", "MEDIUM", "LOW"):
        severity = (
            AnomalySeverity.CRITICAL
            if severity_raw == "HIGH"
            else AnomalySeverity.WARN
            if severity_raw == "MEDIUM"
            else AnomalySeverity.INFO
        )
    else:
        try:
            severity = AnomalySeverity(severity_raw)
        except ValueError:
            severity = AnomalySeverity.WARN

    return AnomalyItem(
        id=row.id,
        detected_at=row.detected_at,
        anomaly_type=str(details.get("anomaly_type", row.metric_name)),
        metric_name=row.metric_name,
        severity=severity,
        observed_value=row.observed_value,
        expected_value=row.expected_value,
        z_score=row.z_score,
        message=str(details.get("message", f"Anomaly on {row.metric_name}")),
        suggested_action=str(details.get("suggested_action", "")),
        details=details,
    )
