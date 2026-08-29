"""Event ingestion service — delegates to app.ingestion.EventIngestor."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion import EventIngestor
from app.state import AppState
from schemas.api import (
    EventBatchRequest,
    EventBatchResponse,
    EventIngestRequest,
    EventIngestResponse,
)


class IngestionService:
    def __init__(self, session: AsyncSession, app_state: AppState) -> None:
        self._ingestor = EventIngestor(session, app_state)

    async def ingest(self, request: EventIngestRequest) -> EventIngestResponse:
        return await self._ingestor.ingest(request)

    async def ingest_batch(self, request: EventBatchRequest) -> EventBatchResponse:
        response = await self._ingestor.ingest_envelopes(
            request.events,
            batch_id=request.batch_id,
        )
        return EventBatchResponse.from_ingest(response)
