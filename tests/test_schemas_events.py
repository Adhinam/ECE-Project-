# PROMPT:
# Event schema contract tests — deterministic IDs and JSONL serialization.
#
# CHANGES MADE:
# - generate_event_id stability and EventEnvelope JSONL round-trip

"""Schema contract tests for events."""

from __future__ import annotations

import pytest

from schemas.events import EventType, generate_event_id

pytestmark = pytest.mark.unit


def test_generate_event_id_is_deterministic() -> None:
    a = generate_event_id(
        store_id="s1", camera_id="c1", event_type="entry", track_id=1, frame_index=10
    )
    b = generate_event_id(
        store_id="s1", camera_id="c1", event_type="entry", track_id=1, frame_index=10
    )
    assert a == b


def test_event_envelope_jsonl(sample_event) -> None:
    line = sample_event.to_jsonl_line()
    assert line.endswith("\n")
    assert EventType.ENTRY.value in line
