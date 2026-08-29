# PROMPT:
# API domain layer tests — ingestion partition, funnel builder, metrics, anomaly z-score.
#
# CHANGES MADE:
# - api.domain.ingestion partition_events
# - api.domain.funnel build_funnel_response mapping
# - api.domain.metrics compute_visitors_inside
# - api.domain.anomalies detect_anomaly thresholds

"""Legacy domain module tests (api.domain.*)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from api.domain.anomalies import detect_anomaly
from api.domain.funnel import build_funnel_response
from api.domain.ingestion import partition_events
from api.domain.metrics import build_realtime_metrics, compute_visitors_inside
from tests.factories import EventFactory


@pytest.mark.unit
def test_partition_events_duplicate(event_factory: EventFactory) -> None:
    e = event_factory.entry()
    new, dups, rejected = partition_events([e], {e.event_id})
    assert len(new) == 0 and len(dups) == 1 and len(rejected) == 0


@pytest.mark.unit
def test_partition_events_new(event_factory: EventFactory) -> None:
    e = event_factory.entry()
    new, dups, _ = partition_events([e], set())
    assert len(new) == 1 and len(dups) == 0


@pytest.mark.unit
def test_funnel_response_conversion() -> None:
    now = datetime.now(timezone.utc)
    resp = build_funnel_response(
        store_id="store-001",
        from_time=now,
        to_time=now,
        stage_counts={"entry": 100, "checkout_proxy": 20},
    )
    assert resp.conversion_rate == 0.2


@pytest.mark.unit
def test_visitors_inside_non_negative() -> None:
    assert compute_visitors_inside(10, 3) == 7
    assert compute_visitors_inside(3, 10) == 0


@pytest.mark.unit
def test_build_realtime_metrics() -> None:
    now = datetime.now(timezone.utc)
    m = build_realtime_metrics(
        store_id="s",
        as_of=now,
        entries=10,
        exits=4,
        conversion_rate=0.2,
        avg_queue_depth=2.0,
        max_queue_depth=5.0,
        staff_excluded=1,
    )
    assert m.visitors_inside == 6


@pytest.mark.unit
def test_detect_anomaly_z_score() -> None:
    hit = detect_anomaly(metric_name="queue_depth", observed=20.0, mean=5.0, std=2.0, threshold=2.5)
    assert hit is not None
    assert hit.severity in ("WARN", "CRITICAL")
