"""Metrics use-case — realtime buckets and store metrics engine."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from api.domain.metrics import build_realtime_metrics
from app.metrics import StoreMetricsEngine
from app.repositories import MetricRepository
from app.state import AppState
from schemas.api import RealtimeMetricsResponse, StoreMetricsResponse
from shared.logging import get_logger

logger = get_logger(__name__)


class MetricsService:
    def __init__(self, session: AsyncSession, app_state: AppState) -> None:
        self._repo = MetricRepository(session)
        self._store_engine = StoreMetricsEngine(session, app_state)
        self._state = app_state

    async def get_store_metrics(
        self,
        store_id: str,
        *,
        window_minutes: int = 15,
        checkout_zone_id: str = "checkout",
    ) -> StoreMetricsResponse:
        return await self._store_engine.get_store_metrics(
            store_id,
            window_minutes=window_minutes,
            checkout_zone_id=checkout_zone_id,
        )

    async def get_realtime(self, store_id: str, window_minutes: int) -> RealtimeMetricsResponse:
        if not self._state.db_available:
            logger.warning("metrics_degraded_no_db", store_id=store_id)
            now = datetime.now(timezone.utc)
            return build_realtime_metrics(
                store_id=store_id,
                as_of=now,
                entries=0,
                exits=0,
                conversion_rate=0.0,
                avg_queue_depth=None,
                max_queue_depth=None,
                staff_excluded=0,
            )

        now = datetime.now(timezone.utc)
        from_time = now - timedelta(minutes=window_minutes)
        buckets = await self._repo.get_buckets(store_id, from_time, now)
        entries = sum(b.entries for b in buckets)
        exits = sum(b.exits for b in buckets)
        avg_q = _avg_optional([b.avg_queue_depth for b in buckets])
        max_q = max((b.max_queue_depth for b in buckets if b.max_queue_depth is not None), default=None)
        conversion = _avg_optional([b.conversion_rate for b in buckets if b.conversion_rate is not None])

        return build_realtime_metrics(
            store_id=store_id,
            as_of=now,
            entries=entries,
            exits=exits,
            conversion_rate=conversion or 0.0,
            avg_queue_depth=avg_q,
            max_queue_depth=max_q,
            staff_excluded=sum(b.staff_excluded for b in buckets),
        )


def _avg_optional(values: list[float | None]) -> float | None:
    nums = [v for v in values if v is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)
