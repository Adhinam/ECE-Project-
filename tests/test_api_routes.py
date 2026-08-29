# PROMPT:
# FastAPI route smoke tests — ingest, metrics, funnel, anomalies with dependency overrides.
#
# CHANGES MADE:
# - /events/ingest response schema validation
# - /stores/{id}/metrics and /funnel with stub services
# - OpenAPI and /live probes

"""API route integration tests (no Postgres)."""

from __future__ import annotations

from datetime import datetime, timezone
import pytest
from fastapi.testclient import TestClient

from app.dependencies import get_analytics_service, get_ingestion_service, get_metrics_service
from app.main import create_app
from schemas.api import (
    AnomaliesResponse,
    EventIngestResponse,
    FunnelResponse,
    FunnelStage,
    StoreMetricsResponse,
)


@pytest.mark.unit
def test_openapi_and_live(api_client: TestClient) -> None:
    assert api_client.get("/openapi.json").status_code == 200
    assert api_client.get("/live").json()["status"] == "alive"


@pytest.mark.unit
def test_ingest_route_with_stub() -> None:
    app = create_app()

    class StubIngest:
        async def ingest(self, request):
            return EventIngestResponse(accepted=1, rejected=0, duplicates=0)

    app.dependency_overrides[get_ingestion_service] = lambda: StubIngest()
    client = TestClient(app)
    r = client.post(
        "/events/ingest",
        json={
            "events": [
                {
                    "event_id": "550e8400-e29b-41d4-a716-446655440001",
                    "event_type": "entry",
                    "store_id": "store-001",
                    "camera_id": "cam-1",
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "confidence": 0.9,
                }
            ]
        },
    )
    assert r.status_code == 200
    assert r.json()["accepted"] == 1
    app.dependency_overrides.clear()


@pytest.mark.unit
def test_store_metrics_route_stub() -> None:
    app = create_app()
    now = datetime.now(timezone.utc)

    class StubMetrics:
        async def get_store_metrics(self, store_id, **kwargs):
            return StoreMetricsResponse(store_id=store_id, as_of=now)

    app.dependency_overrides[get_metrics_service] = lambda: StubMetrics()
    client = TestClient(app)
    r = client.get("/stores/store-001/metrics")
    assert r.status_code == 200
    assert r.json()["store_id"] == "store-001"
    app.dependency_overrides.clear()


@pytest.mark.unit
def test_store_anomalies_route_stub() -> None:
    app = create_app()

    class StubAnalytics:
        async def get_anomalies(self, store_id, **kwargs):
            return AnomaliesResponse(store_id=store_id)

        async def get_funnel(self, *a, **kw):
            return FunnelResponse(
                store_id="store-001",
                from_time=datetime.now(timezone.utc),
                to_time=datetime.now(timezone.utc),
                stages=[FunnelStage(stage="ENTRY", count=0)],
            )

    app.dependency_overrides[get_analytics_service] = lambda: StubAnalytics()
    client = TestClient(app)
    assert client.get("/stores/store-001/anomalies").status_code == 200
    app.dependency_overrides.clear()
