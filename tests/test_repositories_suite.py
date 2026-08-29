# PROMPT:
# Repository layer tests with mocked AsyncSession execute results.
#
# CHANGES MADE:
# - EventRepository insert/find with MagicMock session
# - HealthDataRepository and MetricRepository query helpers
# - AnomalyRepository list_recent

"""Repository tests with mocked SQLAlchemy session."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.repositories import (
    AnomalyDataRepository,
    AnomalyRepository,
    EventRepository,
    HealthDataRepository,
    MetricRepository,
    StoreMetricsRepository,
)
from tests.factories import EventFactory


def _scalar_result(value):
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    r.scalar_one.return_value = value
    return r


def _scalars_all(rows):
    r = MagicMock()
    r.scalars.return_value.all.return_value = rows
    r.all.return_value = rows
    return r


@pytest.mark.unit
@pytest.mark.asyncio
class TestEventRepository:
    async def test_find_existing_ids_empty(self) -> None:
        session = AsyncMock()
        repo = EventRepository(session)
        assert await repo.find_existing_ids([]) == set()

    async def test_insert_events(self, event_factory: EventFactory) -> None:
        session = AsyncMock()
        exec_result = MagicMock()
        exec_result.rowcount = 1
        session.execute = AsyncMock(return_value=exec_result)
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        repo = EventRepository(session)
        e = event_factory.entry()
        n = await repo.insert_events([e], commit=True)
        assert n == 1
        session.execute.assert_called_once()
        session.commit.assert_called_once()


now = datetime.now(timezone.utc)


@pytest.mark.unit
@pytest.mark.asyncio
class TestHealthDataRepository:
    async def test_fetch_global_timestamps(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[_scalar_result(now), _scalar_result(now)])
        repo = HealthDataRepository(session)
        occurred, ingested = await repo.fetch_global_timestamps()
        assert occurred == now and ingested == now

    async def test_count_ingest_batches(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_scalar_result(5))
        repo = HealthDataRepository(session)
        assert await repo.count_ingest_batches_since(now) == 5


@pytest.mark.unit
@pytest.mark.asyncio
class TestStoreMetricsRepository:
    async def test_has_no_activity(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_scalar_result(0))
        repo = StoreMetricsRepository(session)
        assert await repo._has_visitor_activity("s", now, now) is False

    async def test_fetch_session_events_empty(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_scalars_all([]))
        repo = StoreMetricsRepository(session)
        rows = await repo.fetch_session_events("s", now, now)
        assert rows == []


@pytest.mark.unit
@pytest.mark.asyncio
class TestMetricRepository:
    async def test_get_buckets(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_scalars_all([]))
        repo = MetricRepository(session)
        assert await repo.get_buckets("s", now, now) == []


@pytest.mark.unit
@pytest.mark.asyncio
class TestAnomalyRepository:
    async def test_list_recent(self) -> None:
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_scalars_all([]))
        repo = AnomalyRepository(session)
        assert await repo.list_recent("store-001") == []
