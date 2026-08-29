"""Event ingestion routes."""

from fastapi import APIRouter, Depends

from app.dependencies import get_ingestion_service, verify_api_key
from app.services.ingestion import IngestionService
from schemas.api import (
    EventBatchRequest,
    EventBatchResponse,
    EventIngestRequest,
    EventIngestResponse,
)

router = APIRouter(prefix="/events", tags=["events"])


@router.post(
    "/ingest",
    response_model=EventIngestResponse,
    summary="Ingest event batch (partial success)",
    description=(
        "Accepts up to 500 events per request. Valid events are inserted in a single "
        "transaction; schema failures are reported in `validation_errors` without "
        "rolling back accepted rows. Duplicate `event_id` values are counted, not re-inserted."
    ),
    responses={
        200: {"description": "Batch processed (may include partial validation failures)"},
        503: {"description": "Database unavailable or transaction failed"},
    },
)
async def ingest_events(
    body: EventIngestRequest,
    service: IngestionService = Depends(get_ingestion_service),
    _: None = Depends(verify_api_key),
) -> EventIngestResponse:
    return await service.ingest(body)


@router.post(
    "/batch",
    response_model=EventBatchResponse,
    summary="Ingest pre-validated event batch",
    description="Legacy endpoint — same semantics as `/ingest` with typed event envelopes.",
)
async def ingest_batch(
    body: EventBatchRequest,
    service: IngestionService = Depends(get_ingestion_service),
    _: None = Depends(verify_api_key),
) -> EventBatchResponse:
    return await service.ingest_batch(body)
