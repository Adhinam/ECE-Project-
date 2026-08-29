# PROMPT:
# Legacy funnel tests — see test_funnel_suite.py for comprehensive coverage.
#
# CHANGES MADE:
# - Thin smoke re-exports of critical funnel assertions

"""Legacy funnel tests."""

from __future__ import annotations

import pytest

from app.funnel import FunnelStageName, aggregate_funnel_sessions, empty_funnel_response
from tests.factories import VisitorJourney

pytestmark = pytest.mark.unit


def test_empty_funnel_smoke() -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    r = empty_funnel_response("s", from_time=now, to_time=now)
    assert r.is_empty


def test_journey_funnel_smoke(visitor_journey: VisitorJourney) -> None:
    visitor_journey.enter().visit_zone("entrance-lobby").leave()
    agg = aggregate_funnel_sessions(visitor_journey.to_session_rows())
    assert agg.stage_counts[FunnelStageName.ENTRY] == 1
