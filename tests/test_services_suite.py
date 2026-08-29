# PROMPT:
# Service layer tests — analytics, metrics, ingestion wrappers with mocked engines.
#
# CHANGES MADE:
# - AnalyticsService funnel/anomalies/heatmap/queue paths
# - MetricsService realtime and store metrics delegation
# - IngestionService ingest_batch mapping

"""Application service layer tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.analytics import AnalyticsService, _anomaly_row_to_item
from app.services.ingestion import IngestionService
from app.services.metrics import MetricsService
from schemas.api import (
    AnomaliesResponse,
    EventIngestResponse,
    FunnelResponse,
    FunnelStage,
    StoreMetricsResponse,
)


@pytest.mark.unit
@pytest.mark.asyncio
class TestAnalyticsService:
    async def test_get_funnel_delegates(self, app_state) -> None:
        svc = AnalyticsService(MagicMock(), app_state)
        now = datetime.now(timezone.utc)
        expected = FunnelResponse(store_id="s", from_time=now, to_time=now, stages=[])
        svc._funnel.get_funnel = AsyncMock(return_value=expected)
        assert (await svc.get_funnel("s", now, now)).store_id == "s"

    async def test_get_anomalies_live(self, app_state) -> None:
        svc = AnalyticsService(MagicMock(), app_state)
        svc._anomaly_engine.detect = AsyncMock(return_value=AnomaliesResponse(store_id="s"))
        assert (await svc.get_anomalies("s")).store_id == "s"

    async def test_get_heatmap(self, app_state) -> None:
        svc = AnalyticsService(MagicMock(), app_state)
        svc._heatmap._repo.fetch_spatial_samples = AsyncMock(return_value=[])
        now = datetime.now(timezone.utc)
        r = await svc.get_heatmap("s", now, now, 32)
        assert r.resolution == 32
        assert r.data_confidence == "unavailable"

    async def test_get_queue(self, app_state) -> None:
        svc = AnalyticsService(MagicMock(), app_state)
        r = await svc.get_queue("s", "q1", 60)
        assert r.queue_id == "q1"


@pytest.mark.unit
def test_anomaly_row_mapping() -> None:
    row = MagicMock()
    row.id = 1
    row.detected_at = datetime.now(timezone.utc)
    row.metric_name = "queue_depth"
    row.severity = "HIGH"
    row.observed_value = 1.0
    row.expected_value = 0.5
    row.z_score = 2.0
    row.details = {"message": "test", "anomaly_type": "queue_spike"}
    item = _anomaly_row_to_item(row)
    assert item.severity.value == "CRITICAL"


@pytest.mark.unit
@pytest.mark.asyncio
class TestMetricsService:
    async def test_get_store_metrics(self, app_state) -> None:
        svc = MetricsService(MagicMock(), app_state)
        now = datetime.now(timezone.utc)
        svc._store_engine.get_store_metrics = AsyncMock(
            return_value=StoreMetricsResponse(store_id="s", as_of=now),
        )
        assert (await svc.get_store_metrics("s")).store_id == "s"

    async def test_get_realtime_degraded(self, app_state_db_down) -> None:
        svc = MetricsService(MagicMock(), app_state_db_down)
        r = await svc.get_realtime("s", 15)
        assert r.entries == 0


@pytest.mark.unit
@pytest.mark.asyncio
class TestIngestionService:
    async def test_ingest_batch_maps_response(self, app_state, event_factory) -> None:
        svc = IngestionService(MagicMock(), app_state)
        ingest_resp = EventIngestResponse(accepted=2, rejected=0, duplicates=1)
        svc._ingestor.ingest_envelopes = AsyncMock(return_value=ingest_resp)
        r = await svc.ingest_batch(event_factory.ingest_request(event_factory.entry()))
        assert r.accepted == 2 and r.duplicates == 1
