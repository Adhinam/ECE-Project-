# PROMPT:
# Anomaly detection tests — queue spikes, conversion drop, dead zones, stale feeds.
#
# CHANGES MADE:
# - Unit tests for each detector with threshold edge cases
# - Severity mapping INFO/WARN/CRITICAL
# - AnomalyDetectionEngine degraded path without DB

"""Anomaly detection engine tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.anomalies import (
    AnomalyDetectionEngine,
    AnomalyThresholds,
    AnomalyType,
    detect_conversion_drop,
    detect_dead_zones,
    detect_queue_spikes,
    detect_stale_feeds,
    rolling_z_score,
    safe_std,
)
from app.state import AppState
from schemas.api import AnomalySeverity


@pytest.mark.unit
class TestStatHelpers:
    def test_rolling_z_insufficient_history(self) -> None:
        z, mean, _ = rolling_z_score(5.0, [1.0, 2.0], min_samples=5)
        assert z == 0.0

    def test_safe_std_floor(self) -> None:
        assert safe_std([4.0, 4.0, 4.0]) > 0


@pytest.mark.unit
class TestQueueSpike:
    def test_absolute_critical_without_baseline(self) -> None:
        now = datetime.now(timezone.utc)
        hits = detect_queue_spikes(
            current_depths=[("q1", 20)],
            historical_depths=[],
            thresholds=AnomalyThresholds(queue_spike_absolute_critical=15),
            detected_at=now,
        )
        assert hits[0].severity == AnomalySeverity.CRITICAL
        assert hits[0].anomaly_type == AnomalyType.QUEUE_SPIKE


@pytest.mark.unit
class TestConversionDrop:
    def test_no_baseline_safe(self) -> None:
        assert detect_conversion_drop(current_rate=0.1, baseline_rates=[], thresholds=AnomalyThresholds()) is None

    def test_warn_on_drop(self) -> None:
        hit = detect_conversion_drop(
            current_rate=0.10,
            baseline_rates=[0.20, 0.22, 0.18],
            thresholds=AnomalyThresholds(conversion_drop_warn_pct=20.0, min_baseline_samples=3),
        )
        assert hit is not None
        assert hit.severity in (AnomalySeverity.WARN, AnomalySeverity.CRITICAL)


@pytest.mark.unit
class TestDeadZone:
    def test_dead_zone_detected(self) -> None:
        hits = detect_dead_zones(
            current_visits={"lobby": 0},
            baseline_visits={"lobby": 50},
            thresholds=AnomalyThresholds(dead_zone_min_baseline_visits=5),
        )
        assert len(hits) == 1
        assert hits[0].anomaly_type == AnomalyType.DEAD_ZONE


@pytest.mark.unit
class TestStaleFeed:
    def test_store_level_stale_critical(self) -> None:
        now = datetime.now(timezone.utc)
        last = now - timedelta(minutes=90)
        hits = detect_stale_feeds(
            camera_last_event=[("cam-1", last)],
            store_last_event=last,
            now=now,
            thresholds=AnomalyThresholds(stale_feed_minutes_warn=15, stale_feed_minutes_critical=60),
        )
        assert any(h.severity == AnomalySeverity.CRITICAL for h in hits)

    def test_per_camera_stale_when_store_has_recent(self) -> None:
        now = datetime.now(timezone.utc)
        hits = detect_stale_feeds(
            camera_last_event=[
                ("cam-1", now - timedelta(minutes=1)),
                ("cam-2", now - timedelta(minutes=120)),
            ],
            store_last_event=now - timedelta(minutes=1),
            now=now,
            thresholds=AnomalyThresholds(stale_feed_minutes_warn=15),
        )
        assert any("cam-2" in h.metric_name for h in hits)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_degraded(app_state_db_down: AppState) -> None:
    engine = AnomalyDetectionEngine(MagicMock(), app_state_db_down)
    resp = await engine.detect("store-001")
    assert resp.items == []
