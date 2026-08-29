# PROMPT:
# Domain anomaly z-score tests — see test_domain_suite.py.
#
# CHANGES MADE:
# - detect_anomaly WARN/CRITICAL thresholds

"""Domain anomaly tests."""

from __future__ import annotations

import pytest

from api.domain.anomalies import detect_anomaly

pytestmark = pytest.mark.unit


def test_detect_anomaly_triggers_on_high_z() -> None:
    result = detect_anomaly(
        metric_name="queue_depth", observed=20.0, mean=5.0, std=2.0, threshold=2.5
    )
    assert result is not None
    assert result.severity in ("WARN", "CRITICAL")


def test_detect_anomaly_none_when_normal() -> None:
    result = detect_anomaly(
        metric_name="queue_depth", observed=5.5, mean=5.0, std=2.0, threshold=2.5
    )
    assert result is None
