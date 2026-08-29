# PROMPT:
# Isolated ingestion persistence tests using MemoryEventRepository (no Postgres).
#
# CHANGES MADE:
# - Idempotent insert and duplicate detection against in-memory store
# - EventIngestor integration with patched repository for transaction path

"""Isolated DB tests for ingestion persistence."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.ingestion import EventIngestor
from app.state import AppState
from tests.db.memory_repository import MemoryEventRepository, MemoryEventStore
from tests.factories import EventFactory


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_repository_idempotent_insert(memory_store: MemoryEventStore) -> None:
    factory = EventFactory()
    repo = MemoryEventRepository(memory_store)
    e1 = factory.entry()
    e2 = factory.entry(track_id=2)
    assert await repo.insert_events([e1]) == 1
    existing = await repo.find_existing_ids([e1.event_id, e2.event_id])
    assert e1.event_id in existing
    assert e2.event_id not in existing
    assert await repo.insert_events([e2]) == 1
    assert len(memory_store.events) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ingestor_with_memory_repo(
    memory_event_repository: MemoryEventRepository,
    mock_async_session: MagicMock,
    app_state: AppState,
    event_factory: EventFactory,
) -> None:
    ingestor = EventIngestor(mock_async_session, app_state)
    ingestor._repo = memory_event_repository  # type: ignore[assignment]

    req = event_factory.ingest_request(
        event_factory.entry(),
        event_factory.entry(track_id=2),
    )
    resp = await ingestor.ingest(req)
    assert resp.accepted == 2
    assert resp.rejected == 0
    assert len(memory_event_repository._store.batches) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ingestor_duplicate_not_double_inserted(
    memory_event_repository: MemoryEventRepository,
    mock_async_session: MagicMock,
    app_state: AppState,
    event_factory: EventFactory,
) -> None:
    ingestor = EventIngestor(mock_async_session, app_state)
    ingestor._repo = memory_event_repository  # type: ignore[assignment]

    e = event_factory.entry()
    req = event_factory.ingest_request(e)
    first = await ingestor.ingest(req)
    second = await ingestor.ingest(req)
    assert first.accepted == 1
    assert second.accepted == 0
    assert second.duplicates == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ingest_transaction_failure_rolls_up(
    mock_async_session: MagicMock,
    app_state: AppState,
    event_factory: EventFactory,
) -> None:
    ingestor = EventIngestor(mock_async_session, app_state)
    ingestor._repo = MagicMock()
    ingestor._repo.find_existing_ids = AsyncMock(return_value=set())
    ingestor._repo.insert_events = AsyncMock(side_effect=RuntimeError("db write failed"))
    ingestor._repo.record_ingest_batch = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await ingestor.ingest(event_factory.ingest_request(event_factory.entry()))
    assert exc.value.status_code == 503
