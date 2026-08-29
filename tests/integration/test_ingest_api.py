# PROMPT:
# Integration tests for POST /events/ingest — response shape with stubbed service.
#
# CHANGES MADE:
# - Validates 422 on oversized batch and 200 partial-success shape

"""Integration tests for POST /events/ingest."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_ingestion_service
from app.main import create_app
from schemas.api import EventIngestRequest, EventIngestResponse, IngestError

pytestmark = pytest.mark.integration


class _StubIngestionService:
    async def ingest(self, request: EventIngestRequest) -> EventIngestResponse:
        return EventIngestResponse(
            accepted=1,
            rejected=1,
            duplicates=0,
            validation_errors=[IngestError(index=1, message="invalid", code="validation_error")],
            batch_id=request.batch_id or uuid4(),
        )


def test_ingest_endpoint_response_shape() -> None:
    app = create_app()
    app.dependency_overrides[get_ingestion_service] = lambda: _StubIngestionService()
    client = TestClient(app)
    response = client.post(
        "/events/ingest",
        json={"events": [{"valid": False}, {"also": "bad"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 1
    app.dependency_overrides.clear()


def test_ingest_rejects_oversized_batch() -> None:
    client = TestClient(create_app())
    events = [
        {
            "event_id": "550e8400-e29b-41d4-a716-446655440000",
            "event_type": "entry",
            "store_id": "store-001",
            "camera_id": "cam-1",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "confidence": 0.9,
        }
    ] * 501
    response = client.post("/events/ingest", json={"events": events})
    assert response.status_code == 422
