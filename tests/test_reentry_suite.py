# PROMPT:
# Re-entry tests — funnel deduplication, pipeline SessionEngine resume, Re-ID payload contract.
#
# CHANGES MADE:
# - Funnel re-entry window edge cases (outside window starts new session)
# - SessionEngine resume_visitor preserves session_sequence
# - Reentry event payload validation via EventFactory

"""Re-entry handling across funnel and session engines."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest

from app.funnel import FunnelStageName, aggregate_funnel_sessions
from app.metrics import SessionEventRow
from pipeline.session import SessionEngine
from schemas.events import EventType
from tests.factories import EventFactory


@pytest.mark.unit
class TestFunnelReentryWindow:
    def test_reentry_outside_window_counts_new_entry(self, event_factory: EventFactory) -> None:
        vid = str(uuid4())
        t0 = event_factory._t0
        events = [
            event_factory.entry(track_id=1, visitor_id=vid, occurred_at=t0),
            event_factory.exit(track_id=1, visitor_id=vid, occurred_at=t0 + timedelta(seconds=10)),
            event_factory.reentry(vid, track_id=1, occurred_at=t0 + timedelta(seconds=400)),
        ]
        rows = [
            SessionEventRow(
                event_type=e.event_type.value,
                occurred_at=e.occurred_at,
                track_id=e.track_id,
                global_person_id=e.global_person_id,
                is_staff=e.is_staff,
                confidence=e.confidence,
                payload=e.payload,
            )
            for e in events
        ]
        agg = aggregate_funnel_sessions(rows, reentry_window_seconds=300)
        assert agg.stage_counts[FunnelStageName.ENTRY] == 2
        assert agg.reentry_merged == 0


@pytest.mark.unit
class TestSessionEngineReentry:
    def test_resume_visitor_keeps_sequence(self, event_factory: EventFactory) -> None:
        engine = SessionEngine(store_id="store-001")
        entry = event_factory.entry(track_id=5)
        session = engine.process_event(entry)
        assert session is not None
        seq = session.session_sequence
        vid = session.visitor_id

        engine.process_event(event_factory.exit(track_id=5, occurred_at=entry.occurred_at + timedelta(seconds=60)))
        reentry = event_factory.reentry(str(vid), track_id=5, occurred_at=entry.occurred_at + timedelta(seconds=120))
        reentry.payload["visitor_id"] = str(vid)
        resumed = engine.process_event(reentry)
        assert resumed is not None
        assert resumed.visitor_id == vid
        assert resumed.session_sequence == seq

    def test_handle_reentry_from_payload(self, event_factory: EventFactory) -> None:
        engine = SessionEngine(store_id="store-001")
        vid = uuid4()
        ev = event_factory.reentry(str(vid), track_id=1)
        ev.payload["visitor_id"] = str(vid)
        session = engine._handle_reentry(ev)
        assert session.visitor_id == UUID(str(vid))
