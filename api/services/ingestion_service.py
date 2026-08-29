"""Ingestion use-case — idempotent batch processing."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from api.adapters.db.repositories import EventRepository
from api.domain.ingestion import partition_events
from schemas.api import EventBatchRequest, EventBatchResponse
from shared.logging import get_logger

logger = get_logger(__name__)


class IngestionService:
    def __init__(self, session: AsyncSession) -> None:
        self._repo = EventRepository(session)
        self._session = session

    async def ingest_batch(self, request: EventBatchRequest) -> EventBatchResponse:
        event_ids = [e.event_id for e in request.events]
        existing = await self._repo.find_existing_ids(event_ids)
        new_events, duplicates, rejected = partition_events(request.events, existing)

        accepted = await self._repo.insert_events(new_events)
        # TODO: commit, update rollups, anomaly side effects

        logger.info(
            "ingest_batch",
            accepted=accepted,
            duplicates=len(duplicates),
            rejected=len(rejected),
        )
        return EventBatchResponse(
            accepted=accepted,
            duplicates=len(duplicates),
            rejected=len(rejected),
            errors=[],
        )
