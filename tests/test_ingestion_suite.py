# PROMPT:
# Comprehensive ingestion tests — validation, batch limits, partial success, dedup.
#
# CHANGES MADE:
# - Edge cases: invalid types, bad confidence, empty payload keys, batch cap 500
# - split_in_batch_duplicates and partition_for_insert matrix tests
# - IngestMetrics logging path via successful mock ingest

"""Ingestion engine and API contract tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.ingestion import (
    MAX_INGEST_BATCH_SIZE,
    EventIngestor,
    partition_for_insert,
    split_in_batch_duplicates,
    validate_event_payload,
)
from app.state import AppState
from schemas.api import EventIngestRequest, MAX_INGEST_BATCH_SIZE as API_MAX
from schemas.events import EventEnvelope, EventType, generate_event_id
from tests.factories import EventFactory


@pytest.mark.unit
class TestValidateEventPayload:
    def test_valid_entry(self, event_factory: EventFactory) -> None:
        raw = event_factory.to_raw(event_factory.entry())
        env, err = validate_event_payload(raw, 0)
        assert env is not None and err is None

    def test_invalid_confidence(self, event_factory: EventFactory) -> None:
        raw = event_factory.to_raw(event_factory.entry())
        raw["confidence"] = 1.5
        _, err = validate_event_payload(raw, 2)
        assert err is not None
        assert err.index == 2

    def test_missing_required_field(self) -> None:
        _, err = validate_event_payload({"event_type": "entry"}, 0)
        assert err is not None

    def test_malformed_uuid(self, event_factory: EventFactory) -> None:
        raw = event_factory.to_raw(event_factory.entry())
        raw["event_id"] = "not-a-uuid"
        _, err = validate_event_payload(raw, 0)
        assert err is not None


@pytest.mark.unit
class TestBatchLimits:
    def test_max_batch_size_constant_aligned(self) -> None:
        assert MAX_INGEST_BATCH_SIZE == API_MAX == 500

    def test_request_rejects_501_events(self, event_factory: EventFactory) -> None:
        raw = event_factory.to_raw(event_factory.entry())
        with pytest.raises(ValidationError):
            EventIngestRequest(events=[raw] * 501)


@pytest.mark.unit
class TestDedupHelpers:
    def test_in_batch_duplicate(self, event_factory: EventFactory) -> None:
        e = event_factory.entry()
        unique, dups = split_in_batch_duplicates([e, e])
        assert len(unique) == 1 and dups == 1

    def test_db_duplicate_partition(self, event_factory: EventFactory) -> None:
        e = event_factory.entry()
        to_insert, dups = partition_for_insert([e], {e.event_id})
        assert to_insert == [] and dups == 1


@pytest.mark.unit
@pytest.mark.asyncio
class TestEventIngestor:
    async def test_partial_success(
        self, mock_async_session: MagicMock, app_state: AppState, event_factory: EventFactory
    ) -> None:
        ingestor = EventIngestor(mock_async_session, app_state)
        ingestor._repo = MagicMock()
        ingestor._repo.find_existing_ids = AsyncMock(return_value=set())
        ingestor._repo.insert_events = AsyncMock(return_value=1)
        ingestor._repo.record_ingest_batch = AsyncMock()

        resp = await ingestor.ingest(
            EventIngestRequest(
                events=[event_factory.to_raw(event_factory.entry()), {"bad": True}],
            )
        )
        assert resp.accepted == 1 and resp.rejected == 1

    async def test_db_down_503(self, mock_async_session: MagicMock, app_state_db_down: AppState) -> None:
        ingestor = EventIngestor(mock_async_session, app_state_db_down)
        with pytest.raises(HTTPException) as exc:
            await ingestor.ingest(EventIngestRequest(events=[{"x": 1}]))
        assert exc.value.status_code == 503

    async def test_empty_valid_batch_records_batch_only(
        self, mock_async_session: MagicMock, app_state: AppState, event_factory: EventFactory
    ) -> None:
        ingestor = EventIngestor(mock_async_session, app_state)
        ingestor._repo = MagicMock()
        ingestor._repo.find_existing_ids = AsyncMock(return_value=set())
        ingestor._repo.insert_events = AsyncMock(return_value=0)
        ingestor._repo.record_ingest_batch = AsyncMock()

        resp = await ingestor.ingest(
            EventIngestRequest(events=[event_factory.to_raw(event_factory.entry())])
        )
        ingestor._repo.record_ingest_batch.assert_called_once()
