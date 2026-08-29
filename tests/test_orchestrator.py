# PROMPT:
# Pipeline orchestrator unit tests — staff ROI and visitor_id enrichment helpers.
#
# CHANGES MADE:
# - _is_staff_track respects staff_areas polygon
# - _enrich_visitor_payload injects visitor_id after session assignment

"""Full pipeline orchestrator helper tests."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from pipeline.orchestrator import _enrich_visitor_payload, _is_staff_track
from pipeline.session import SessionEngine
from schemas.config import StaffAreaConfig, StoreLayoutConfig
from schemas.events import EventType, generate_event_id
from tests.factories import EventFactory


@pytest.mark.unit
def test_staff_track_inside_roi() -> None:
    layout = StoreLayoutConfig(
        store_id="store-001",
        staff_areas=[StaffAreaConfig(id="bo", polygon=[[0, 0], [100, 0], [100, 100], [0, 100]])],
    )
    assert _is_staff_track(layout, (10, 10, 50, 50)) is True
    assert _is_staff_track(layout, (200, 200, 250, 250)) is False


@pytest.mark.unit
def test_enrich_visitor_payload_after_entry(event_factory: EventFactory) -> None:
    session = SessionEngine("store-001")
    entry = event_factory.entry(track_id=7)
    session.process_event(entry)
    zone = event_factory.zone_enter("aisle-1", track_id=7)
    enriched = _enrich_visitor_payload(zone, session)
    assert "visitor_id" in enriched.payload
