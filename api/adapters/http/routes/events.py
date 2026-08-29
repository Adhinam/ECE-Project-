"""Event ingestion routes."""

from fastapi import APIRouter, Depends

from api.dependencies import get_ingestion_service, verify_api_key
from api.services.ingestion_service import IngestionService
from schemas.api import EventBatchRequest, EventBatchResponse

router = APIRouter(prefix="/events", tags=["events"])


@router.post("/batch", response_model=EventBatchResponse)
async def ingest_batch(
    body: EventBatchRequest,
    service: IngestionService = Depends(get_ingestion_service),
    _: None = Depends(verify_api_key),
) -> EventBatchResponse:
    return await service.ingest_batch(body)
