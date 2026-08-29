"""Metrics use-case — real-time rollup queries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.db.repositories import MetricRepository
from api.domain.metrics import build_realtime_metrics
from schemas.api import RealtimeMetricsResponse


class MetricsService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = MetricRepository(session)

    async def get_realtime(self, store_id: str, window_minutes: int) -> RealtimeMetricsResponse:
        now = datetime.now(timezone.utc)
        from_time = now - timedelta(minutes=window_minutes)
        buckets = await self._repo.get_buckets(store_id, from_time, now)
        entries = sum(b.entries for b in buckets)
        exits = sum(b.exits for b in buckets)
        # TODO: aggregate from buckets
        return build_realtime_metrics(
            store_id=store_id,
            as_of=now,
            entries=entries,
            exits=exits,
            conversion_rate=0.0,
            avg_queue_depth=None,
            max_queue_depth=None,
            staff_excluded=0,
        )
