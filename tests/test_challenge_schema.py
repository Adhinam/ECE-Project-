# PROMPT:
# Validate Purplle challenge event JSON round-trips through challenge_events adapter.
#
# CHANGES MADE:
# - challenge_to_envelope / envelope_to_challenge symmetry
# - parse_event_dict accepts internal and challenge shapes

"""Challenge event schema tests."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from schemas.challenge_events import (
    CHALLENGE_BILLING_QUEUE_JOIN,
    CHALLENGE_ENTRY,
    CHALLENGE_QUEUE_DEPTH,
    challenge_to_envelope,
    envelope_to_challenge,
    format_visitor_id,
    is_challenge_event,
    parse_event_dict,
)
from schemas.events import EventEnvelope, EventType


@pytest.mark.unit
def test_format_visitor_id() -> None:
    uid = uuid4()
    assert format_visitor_id(uid) == f"VIS_{uid.hex[:6]}"


@pytest.mark.unit
def test_is_challenge_event() -> None:
    assert is_challenge_event({"visitor_id": "VIS_abc", "timestamp": "2026-01-01T00:00:00Z"})
    assert not is_challenge_event({"occurred_at": "2026-01-01T00:00:00Z"})


@pytest.mark.unit
def test_challenge_round_trip() -> None:
    vid = uuid4()
    raw = {
        "event_id": str(uuid4()),
        "store_id": "store-001",
        "camera_id": "cam-1",
        "visitor_id": format_visitor_id(vid),
        "event_type": CHALLENGE_BILLING_QUEUE_JOIN,
        "timestamp": "2026-01-15T10:00:00+00:00",
        "zone_id": "checkout-1",
        "dwell_ms": 0,
        "is_staff": False,
        "confidence": 0.9,
        "metadata": {"queue_depth": 3},
    }
    env = challenge_to_envelope(raw)
    assert env.event_type == EventType.QUEUE_JOIN
    assert env.payload.get("queue_depth") == 3
    back = envelope_to_challenge(env)
    assert back.event_type == CHALLENGE_BILLING_QUEUE_JOIN
    assert back.visitor_id == format_visitor_id(vid)


@pytest.mark.unit
def test_envelope_to_challenge_restores_misclassified_queue_depth() -> None:
    env = EventEnvelope(
        event_id=uuid4(),
        event_type=EventType.ENTRY,
        store_id="store1",
        camera_id="cam-zone-1",
        occurred_at=datetime.now(timezone.utc),
        confidence=1.0,
        global_person_id="VIS_abc123",
        payload={"queue_depth": 2, "visitor_id": "VIS_abc123"},
    )
    back = envelope_to_challenge(env)
    assert back.event_type == CHALLENGE_QUEUE_DEPTH
    assert back.metadata.queue_depth == 2


@pytest.mark.unit
def test_envelope_to_challenge_keeps_real_entry() -> None:
    env = EventEnvelope(
        event_id=uuid4(),
        event_type=EventType.ENTRY,
        store_id="store1",
        camera_id="cam-billing",
        occurred_at=datetime.now(timezone.utc),
        confidence=0.9,
        global_person_id="VIS_real01",
        payload={"visitor_id": "VIS_real01", "line_id": "main-entrance"},
    )
    back = envelope_to_challenge(env)
    assert back.event_type == CHALLENGE_ENTRY


@pytest.mark.unit
def test_parse_event_dict_internal() -> None:
    eid = uuid4()
    env = EventEnvelope(
        event_id=eid,
        event_type=EventType.ENTRY,
        store_id="s",
        camera_id="c",
        occurred_at=datetime.now(timezone.utc),
        confidence=0.8,
    )
    parsed = parse_event_dict(env.model_dump(mode="json"))
    assert parsed.event_id == eid
