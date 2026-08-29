# PROMPT:
# Legacy store metrics tests — see test_metrics_suite.py.
#
# CHANGES MADE:
# - Smoke test for empty_store_response

"""Legacy metrics tests."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.metrics import empty_store_response

pytestmark = pytest.mark.unit


def test_empty_store_smoke() -> None:
    now = datetime.now(timezone.utc)
    r = empty_store_response("store-001", as_of=now, window_minutes=15)
    assert r.is_empty and r.conversion_rate == 0.0
