# PROMPT:
# Legacy health tests — see test_health_suite.py and test_stale_feed_suite.py.
#
# CHANGES MADE:
# - Smoke tests for status aggregation helpers

"""Legacy health tests."""

from __future__ import annotations

import pytest

from app.health import compute_overall_status

pytestmark = pytest.mark.unit


def test_overall_down_smoke() -> None:
    assert compute_overall_status(
        db_status="down",
        ingestion_status="ok",
        global_stale=False,
        store_statuses=[],
        degraded_features=[],
    ) == "down"
