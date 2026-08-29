# PROMPT:
# Funnel analytics tests — stage transitions, re-entry dedup, staff skip, empty funnel.
#
# CHANGES MADE:
# - Full happy-path funnel via VisitorJourney
# - Re-entry merges sessions (no double ENTRY count)
# - Purchase requires billing queue; staff events ignored
# - dropoff/conversion percentage math

"""Funnel engine comprehensive tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.funnel import (
    FunnelAnalyticsEngine,
    FunnelStageName,
    aggregate_funnel_sessions,
    build_funnel_stages,
    empty_funnel_response,
)
from app.metrics import SessionEventRow
from schemas.events import EventType
from tests.factories import EventFactory, VisitorJourney


def _ts(offset: int = 0) -> datetime:
    return datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset)


def _row_from_factory(factory: EventFactory, envelope) -> SessionEventRow:
    return SessionEventRow(
        event_type=envelope.event_type.value,
        occurred_at=envelope.occurred_at,
        track_id=envelope.track_id,
        global_person_id=envelope.global_person_id,
        is_staff=envelope.is_staff,
        confidence=envelope.confidence,
        payload=envelope.payload,
    )


@pytest.mark.unit
class TestFunnelAggregation:
    def test_empty_funnel(self) -> None:
        resp = empty_funnel_response("s", from_time=_ts(), to_time=_ts(60))
        assert resp.is_empty and all(s.count == 0 for s in resp.stages)

    def test_full_happy_path(self, visitor_journey: VisitorJourney) -> None:
        visitor_journey.enter().visit_zone("entrance-lobby").join_queue().checkout().leave()
        agg = aggregate_funnel_sessions(visitor_journey.to_session_rows())
        assert agg.stage_counts[FunnelStageName.PURCHASE] == 1

    def test_reentry_dedup(self, event_factory: EventFactory) -> None:
        vid = "550e8400-e29b-41d4-a716-446655440000"
        events = [
            event_factory.entry(track_id=1, visitor_id=vid, occurred_at=_ts(0)),
            event_factory.exit(track_id=1, visitor_id=vid, occurred_at=_ts(10)),
            event_factory.reentry(vid, track_id=1, occurred_at=_ts(20)),
            event_factory.zone_enter("entrance-lobby", track_id=1, visitor_id=vid, occurred_at=_ts(21)),
        ]
        rows = [_row_from_factory(event_factory, e) for e in events]
        agg = aggregate_funnel_sessions(rows, reentry_window_seconds=300)
        assert agg.stage_counts[FunnelStageName.ENTRY] == 1
        assert agg.reentry_merged == 1

    def test_staff_not_counted(self, event_factory: EventFactory) -> None:
        rows = [
            _row_from_factory(event_factory, event_factory.entry()),
            _row_from_factory(event_factory, event_factory.staff_entry()),
        ]
        agg = aggregate_funnel_sessions(rows)
        assert agg.stage_counts[FunnelStageName.ENTRY] == 1
        assert agg.staff_excluded == 1

    def test_purchase_requires_queue(self, event_factory: EventFactory) -> None:
        rows = [
            _row_from_factory(event_factory, event_factory.entry(occurred_at=_ts(0))),
            _row_from_factory(event_factory, event_factory.zone_enter("checkout", occurred_at=_ts(1))),
        ]
        agg = aggregate_funnel_sessions(rows)
        assert agg.stage_counts[FunnelStageName.PURCHASE] == 0

    def test_dropoff_percentages(self) -> None:
        counts = {
            FunnelStageName.ENTRY: 100,
            FunnelStageName.ZONE_VISIT: 80,
            FunnelStageName.BILLING_QUEUE: 40,
            FunnelStageName.PURCHASE: 20,
        }
        stages = build_funnel_stages(counts)
        assert stages[0].dropoff_percent == 20.0
        assert stages[-1].conversion_percent == 20.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_funnel_engine_empty_db(mock_async_session, app_state) -> None:
    from unittest.mock import AsyncMock, MagicMock

    engine = FunnelAnalyticsEngine(MagicMock(), app_state)
    engine._repo = MagicMock()
    engine._repo.fetch_session_events = AsyncMock(return_value=[])
    resp = await engine.get_funnel("store-001", from_time=_ts(), to_time=_ts(3600))
    assert resp.is_empty is True
