# PROMPT:
# Stale feed tests — health checker, anomaly detector, per-store and per-camera silence.
#
# CHANGES MADE:
# - detect_stale_feeds edge cases (no history, store vs camera)
# - HealthChecker.build_store_health stale minutes
# - compute_overall_status degraded when global_stale

"""Stale feed detection across health and anomaly modules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.anomalies import AnomalyThresholds, detect_stale_feeds
from app.health import (
    HealthThresholds,
    _StoreSignals,
    build_store_health,
    compute_overall_status,
)
from schemas.api import AnomalySeverity


@pytest.mark.unit
class TestAnomalyStaleFeed:
    def test_no_camera_history_critical(self) -> None:
        now = datetime.now(timezone.utc)
        hits = detect_stale_feeds(
            camera_last_event=[],
            store_last_event=None,
            now=now,
            thresholds=AnomalyThresholds(),
        )
        assert hits[0].severity == AnomalySeverity.CRITICAL

    def test_camera_warn_not_critical(self) -> None:
        now = datetime.now(timezone.utc)
        hits = detect_stale_feeds(
            camera_last_event=[("cam-1", now - timedelta(minutes=20))],
            store_last_event=now - timedelta(minutes=1),
            now=now,
            thresholds=AnomalyThresholds(stale_feed_minutes_warn=15, stale_feed_minutes_critical=60),
        )
        assert hits and hits[0].severity == AnomalySeverity.WARN


@pytest.mark.unit
class TestHealthStaleFeed:
    def test_store_health_stale(self) -> None:
        now = datetime.now(timezone.utc)
        signals = _StoreSignals(
            store_id="store-001",
            latest_event_at=now - timedelta(minutes=45),
            latest_ingested_at=now - timedelta(seconds=30),
        )
        health = build_store_health(signals, now=now, thresholds=HealthThresholds())
        assert health.stale_feed is True
        assert health.status in ("degraded", "down")

    def test_overall_degraded_when_stale(self) -> None:
        status = compute_overall_status(
            db_status="up",
            ingestion_status="ok",
            global_stale=True,
            store_statuses=["ok"],
            degraded_features=["stale_feed"],
        )
        assert status == "degraded"

    def test_fresh_store_ok(self) -> None:
        now = datetime.now(timezone.utc)
        signals = _StoreSignals(
            store_id="store-001",
            latest_event_at=now - timedelta(minutes=2),
            latest_ingested_at=now - timedelta(seconds=5),
            cameras={"cam-1": now - timedelta(minutes=1)},
        )
        health = build_store_health(signals, now=now, thresholds=HealthThresholds())
        assert health.status == "ok"
        assert health.stale_feed is False
