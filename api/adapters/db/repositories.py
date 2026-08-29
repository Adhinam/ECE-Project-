"""Repository layer — persistence only, no business rules."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Anomaly, MetricBucket, RawEvent
from schemas.events import EventEnvelope


class EventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_existing_ids(self, event_ids: list[UUID]) -> set[UUID]:
        if not event_ids:
            return set()
        stmt = select(RawEvent.event_id).where(RawEvent.event_id.in_(event_ids))
        result = await self._session.execute(stmt)
        return set(result.scalars().all())

    async def insert_events(self, events: list[EventEnvelope]) -> int:
        # TODO: bulk insert + rollup side effects
        _ = events
        return 0

    async def get_latest_event_time(self, store_id: str) -> datetime | None:
        # TODO: SELECT MAX(occurred_at)
        _ = store_id
        return None


class MetricRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_buckets(
        self, store_id: str, from_time: datetime, to_time: datetime
    ) -> list[MetricBucket]:
        _ = (store_id, from_time, to_time)
        return []


class AnomalyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_recent(self, store_id: str, limit: int = 20) -> list[Anomaly]:
        _ = (store_id, limit)
        return []

    async def insert(self, anomaly: Anomaly) -> Anomaly:
        self._session.add(anomaly)
        return anomaly
