"""Event ingestion — validation, deduplication, transaction-safe persistence."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import EventRepository
from app.state import AppState
from schemas.api import EventIngestRequest, EventIngestResponse, IngestError, MAX_INGEST_BATCH_SIZE
from schemas.challenge_events import parse_event_dict
from schemas.events import EventEnvelope
from shared.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class IngestMetrics:
    """Per-request ingestion metrics (also emitted in structured logs)."""

    batch_size: int
    validated: int
    accepted: int
    rejected: int
    duplicates: int
    latency_ms: float


def _safe_event_id(raw: dict[str, Any]) -> UUID | None:
    event_id = raw.get("event_id")
    if event_id is None:
        return None
    try:
        return UUID(str(event_id))
    except (ValueError, TypeError):
        return None


def validate_event_payload(
    raw: dict[str, Any],
    index: int,
) -> tuple[EventEnvelope | None, IngestError | None]:
    """Validate challenge or internal event JSON; malformed items do not affect siblings."""
    try:
        return parse_event_dict(raw), None
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(part) for part in first.get("loc", ()))
        msg = first.get("msg", str(exc))
        detail = f"{loc}: {msg}" if loc else msg
        return None, IngestError(
            event_id=_safe_event_id(raw),
            index=index,
            message=detail,
            code="validation_error",
        )


def split_in_batch_duplicates(
    events: list[EventEnvelope],
) -> tuple[list[EventEnvelope], int]:
    """Keep first occurrence per event_id; later copies count as duplicates."""
    seen: set[UUID] = set()
    unique: list[EventEnvelope] = []
    duplicates = 0
    for event in events:
        if event.event_id in seen:
            duplicates += 1
        else:
            seen.add(event.event_id)
            unique.append(event)
    return unique, duplicates


def partition_for_insert(
    events: list[EventEnvelope],
    existing_ids: set[UUID],
) -> tuple[list[EventEnvelope], int]:
    """Split unique valid events into inserts vs database duplicates."""
    to_insert: list[EventEnvelope] = []
    duplicates = 0
    for event in events:
        if event.event_id in existing_ids:
            duplicates += 1
        else:
            to_insert.append(event)
    return to_insert, duplicates


class EventIngestor:
    """
    Idempotent batch ingestion with partial success.

    - Malformed events are isolated (validation_errors, no DB impact).
    - Valid events are inserted in a single transaction (all-or-nothing on DB).
  """

    def __init__(self, session: AsyncSession, app_state: AppState) -> None:
        self._session = session
        self._repo = EventRepository(session)
        self._state = app_state

    async def ingest(self, request: EventIngestRequest) -> EventIngestResponse:
        if not self._state.db_available:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database unavailable — ingestion disabled (degraded mode)",
            )

        started = time.perf_counter()
        batch_id = request.batch_id or uuid4()
        validation_errors: list[IngestError] = []
        valid_events: list[EventEnvelope] = []

        for index, raw in enumerate(request.events):
            if not isinstance(raw, dict):
                validation_errors.append(
                    IngestError(
                        index=index,
                        message="Each event must be a JSON object",
                        code="invalid_type",
                    )
                )
                continue
            envelope, error = validate_event_payload(raw, index)
            if error is not None:
                validation_errors.append(error)
            else:
                valid_events.append(envelope)

        unique_valid, in_batch_duplicates = split_in_batch_duplicates(valid_events)

        event_ids = [e.event_id for e in unique_valid]
        existing_ids = await self._repo.find_existing_ids(event_ids)
        to_insert, db_duplicates = partition_for_insert(unique_valid, existing_ids)
        duplicate_count = in_batch_duplicates + db_duplicates

        accepted = 0
        try:
            if to_insert:
                accepted = await self._repo.insert_events(
                    to_insert,
                    commit=False,
                )
            
            concurrent_dupes = len(to_insert) - accepted
            if concurrent_dupes > 0:
                duplicate_count += concurrent_dupes
                logger.info(
                    "ingest_concurrent_duplicates",
                    batch_id=str(batch_id),
                    skipped=concurrent_dupes,
                )

            await self._repo.record_ingest_batch(
                batch_id,
                accepted=accepted,
                duplicates=duplicate_count,
                rejected=len(validation_errors),
                commit=False,
            )
            await self._session.commit()
        except Exception as exc:
            await self._session.rollback()
            logger.error(
                "ingest_transaction_failed",
                batch_id=str(batch_id),
                error=str(exc),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to persist events — transaction rolled back",
            ) from exc
        

        latency_ms = (time.perf_counter() - started) * 1000
        metrics = IngestMetrics(
            batch_size=len(request.events),
            validated=len(valid_events),
            accepted=accepted,
            rejected=len(validation_errors),
            duplicates=duplicate_count,
            latency_ms=round(latency_ms, 2),
        )
        ingest_store = valid_events[0].store_id if valid_events else None
        _log_ingest_metrics(batch_id, metrics, store_id=ingest_store)

        self._state.note_pipeline_events([e.occurred_at for e in valid_events])

        return EventIngestResponse(
            accepted=accepted,
            rejected=len(validation_errors),
            duplicates=duplicate_count,
            validation_errors=validation_errors,
            batch_id=batch_id,
        )

    async def ingest_envelopes(
        self,
        events: list[EventEnvelope],
        *,
        batch_id: UUID | None = None,
    ) -> EventIngestResponse:
        """Ingest pre-validated envelopes (legacy /v1/events/batch)."""
        raw_request = EventIngestRequest(
            batch_id=batch_id,
            events=[e.model_dump(mode="json") for e in events],
        )
        return await self.ingest(raw_request)


def _log_ingest_metrics(
    batch_id: UUID,
    metrics: IngestMetrics,
    *,
    store_id: str | None = None,
) -> None:
    logger.info(
        "ingest_complete",
        batch_id=str(batch_id),
        endpoint="/events/ingest",
        store_id=store_id,
        event_count=metrics.batch_size,
        batch_size=metrics.batch_size,
        validated=metrics.validated,
        accepted=metrics.accepted,
        rejected=metrics.rejected,
        duplicates=metrics.duplicates,
        latency_ms=metrics.latency_ms,
        status_code=200,
        success_rate=(
            metrics.accepted / metrics.batch_size if metrics.batch_size else 0.0
        ),
    )
