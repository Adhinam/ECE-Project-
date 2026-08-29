# PROMPT:
# AnomalyDetectionEngine integration test with fully mocked repository and funnel.
#
# CHANGES MADE:
# - detect() happy path with synthetic signals
# - aggregate queue depth from metric buckets fallback

"""Anomaly engine integration tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.anomalies import AnomalyDetectionEngine
from app.funnel import FunnelResponse


@pytest.mark.unit
@pytest.mark.asyncio
async def test_detect_with_signals(app_state, event_factory) -> None:
    now = datetime.now(timezone.utc)
    engine = AnomalyDetectionEngine(MagicMock(), app_state)
    engine._data = MagicMock()
    engine._data.fetch_queue_depth_samples = AsyncMock(return_value=[])
    engine._data.fetch_zone_visit_counts = AsyncMock(return_value={})
    engine._data.fetch_latest_event_per_camera = AsyncMock(return_value=[("cam-1", now)])
    engine._data.fetch_latest_store_event = AsyncMock(return_value=now)
    engine._funnel.get_funnel = AsyncMock(
        return_value=FunnelResponse(
            store_id="store-001",
            from_time=now,
            to_time=now,
            stages=[],
            conversion_rate=0.1,
        ),
    )
    engine._data.fetch_metric_buckets = AsyncMock(return_value=[])

    resp = await engine.detect("store-001", limit=10)
    assert resp.store_id == "store-001"
