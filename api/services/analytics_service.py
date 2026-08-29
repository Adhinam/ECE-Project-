"""Analytics use-case — funnel, heatmap, queue, anomalies."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.db.repositories import AnomalyRepository
from api.domain.funnel import build_funnel_response
from schemas.api import (
    AnomaliesResponse,
    AnomalyItem,
    FunnelResponse,
    HeatmapResponse,
    QueueAnalyticsResponse,
)


class AnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self._anomalies = AnomalyRepository(session)

    async def get_funnel(
        self, store_id: str, from_time: datetime, to_time: datetime
    ) -> FunnelResponse:
        # TODO: query funnel_counts or compute from raw_events
        return build_funnel_response(
            store_id=store_id,
            from_time=from_time,
            to_time=to_time,
            stage_counts={"entry": 0, "browsing": 0, "queue": 0, "checkout_proxy": 0, "exit": 0},
        )

    async def get_heatmap(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
        resolution: int,
    ) -> HeatmapResponse:
        return HeatmapResponse(
            store_id=store_id,
            resolution=resolution,
            from_time=from_time,
            to_time=to_time,
            cells=[],
        )

    async def get_queue(
        self, store_id: str, queue_id: str, window_minutes: int
    ) -> QueueAnalyticsResponse:
        return QueueAnalyticsResponse(store_id=store_id, queue_id=queue_id)

    async def get_anomalies(self, store_id: str, limit: int) -> AnomaliesResponse:
        rows = await self._anomalies.list_recent(store_id, limit)
        items = [
            {
                "id": r.id,
                "detected_at": r.detected_at,
                "metric_name": r.metric_name,
                "severity": r.severity,
                "observed_value": r.observed_value,
                "expected_value": r.expected_value,
                "z_score": r.z_score,
                "details": r.details,
            }
            for r in rows
        ]
        return AnomaliesResponse(
            store_id=store_id,
            items=[AnomalyItem(**i) for i in items],
        )
