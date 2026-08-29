# PROMPT:
# Unit tests for visitor session engine (ENTRY/EXIT, zones, reentry resume).
#
# CHANGES MADE:
# - Session lifecycle, timeout, and track-to-visitor mapping coverage

"""Unit tests for visitor session engine."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from pipeline.session import (
    SessionEngine,
    SessionSettings,
    SessionStatus,
    session_from_dict,
    session_to_dict,
    session_to_json,
    sessions_from_jsonl,
    sessions_to_jsonl,
)
from schemas.events import EventEnvelope, EventType, generate_event_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _event(
    event_type: EventType,
    track_id: int,
    frame: int,
    *,
    payload: dict | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_id=generate_event_id(
            store_id="store-001",
            camera_id="cam-1",
            event_type=event_type.value,
            track_id=track_id,
            frame_index=frame,
        ),
        event_type=event_type,
        store_id="store-001",
        camera_id="cam-1",
        occurred_at=_now() + timedelta(seconds=frame),
        track_id=track_id,
        global_person_id=f"track-{track_id}",
        confidence=0.9,
        payload=payload or {},
    )


def test_entry_creates_active_session():
    engine = SessionEngine("store-001")
    session = engine.process_event(
        _event(EventType.ENTRY, 1, 1, payload={"line_id": "main"})
    )
    assert session is not None
    assert session.status == SessionStatus.ACTIVE
    assert isinstance(session.visitor_id, UUID)
    assert session.session_sequence == 1
    assert len(engine.active_sessions) == 1


def test_exit_completes_session():
    engine = SessionEngine("store-001")
    engine.process_event(_event(EventType.ENTRY, 2, 1))
    completed = engine.process_event(_event(EventType.EXIT, 2, 50, payload={"line_id": "main"}))
    assert completed is not None
    assert completed.status == SessionStatus.COMPLETED
    assert completed.exit_at is not None
    assert len(engine.active_sessions) == 0
    assert len(engine.completed_sessions) == 1


def test_zone_dwell_tracked():
    engine = SessionEngine("store-001")
    engine.process_event(_event(EventType.ENTRY, 3, 1))
    engine.process_event(
        _event(
            EventType.ZONE_ENTER,
            3,
            10,
            payload={"zone_id": "lobby", "session_sequence": 1},
        )
    )
    engine.process_event(
        _event(
            EventType.ZONE_DWELL,
            3,
            40,
            payload={"zone_id": "lobby", "dwell_seconds": 30.0, "session_sequence": 1},
        )
    )
    session = engine.get_active_by_track(3)
    assert session is not None
    assert session.total_dwell_seconds >= 30.0
    assert "lobby" in session.zone_ids_visited()


def test_session_timeout():
    settings = SessionSettings(timeout_seconds=1.0, max_completed=100)
    engine = SessionEngine("store-001", settings)
    engine.process_event(_event(EventType.ENTRY, 4, 1))
    future = _event(EventType.ZONE_DWELL, 4, 2, payload={"zone_id": "z", "dwell_seconds": 1})
    future = future.model_copy(update={"occurred_at": _now() + timedelta(seconds=120)})
    engine.process_event(future)
    assert len(engine.active_sessions) == 0
    assert engine.completed_sessions[-1].status == SessionStatus.TIMED_OUT


def test_visitor_summaries():
    engine = SessionEngine("store-001")
    engine.process_event(_event(EventType.ENTRY, 5, 1))
    summaries = engine.visitor_summaries()
    assert len(summaries) == 1
    assert summaries[0].session_sequence == 1


def test_serialization_roundtrip():
    engine = SessionEngine("store-001")
    session = engine.process_event(_event(EventType.ENTRY, 6, 1))
    assert session is not None
    data = session_to_dict(session)
    restored = session_from_dict(data)
    assert restored.visitor_id == session.visitor_id
    json_str = session_to_json(session)
    assert "visitor_id" in json_str


def test_jsonl_roundtrip():
    engine = SessionEngine("store-001")
    s1 = engine.process_event(_event(EventType.ENTRY, 7, 1))
    s2 = engine.process_event(_event(EventType.ENTRY, 8, 2))
    assert s1 and s2
    text = sessions_to_jsonl([s1, s2])
    loaded = sessions_from_jsonl(text)
    assert len(loaded) == 2
    assert loaded[0].session_sequence == 1


def test_session_sequence_increments():
    engine = SessionEngine("store-001")
    s1 = engine.process_event(_event(EventType.ENTRY, 10, 1))
    engine.process_event(_event(EventType.EXIT, 10, 5))
    s2 = engine.process_event(_event(EventType.ENTRY, 11, 6))
    assert s1 and s2
    assert s2.session_sequence == 2
