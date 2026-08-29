# PROMPT:
# Legacy anomaly tests — see test_anomaly_suite.py for full detector coverage.
#
# CHANGES MADE:
# - Smoke tests for queue spike and stale feed detectors

"""Legacy anomaly tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.anomalies import AnomalyThresholds, detect_queue_spikes, detect_stale_feeds
from schemas.api import AnomalySeverity

pytestmark = pytest.mark.unit


def test_queue_spike_smoke() -> None:
    now = datetime.now(timezone.utc)
    hits = detect_queue_spikes(
        current_depths=[("q", 18)],
        historical_depths=[],
        thresholds=AnomalyThresholds(),
        detected_at=now,
    )
    assert hits[0].severity == AnomalySeverity.CRITICAL


def test_stale_smoke() -> None:
    now = datetime.now(timezone.utc)
    hits = detect_stale_feeds(
        camera_last_event=[],
        store_last_event=None,
        now=now,
        thresholds=AnomalyThresholds(),
    )
    assert hits
