# PROMPT:
# Tests for pipeline/schemas.py and pipeline/emit.py including challenge JSONL export.
#
# CHANGES MADE:
# - build_event deterministic ids, validate_batch, StructuredEventEmitter flush

"""Tests for pipeline/schemas.py and pipeline/emit.py."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest

from pipeline.emit import (
    AppendOnlyEventLog,
    EmitSettings,
    EventDedupStore,
    StructuredEventEmitter,
    iter_log_events,
    load_log_events,
)
from pipeline.schemas import (
    EventBuildParams,
    EventMetadata,
    EventType,
    build_event,
    event_to_jsonl_line,
    filter_duplicates,
    parse_jsonl_line,
    timestamp_for_frame,
)
from schemas.events import EventEnvelope


def _sample_event(**kwargs) -> EventEnvelope:
    defaults = dict(
        event_type=EventType.ENTRY,
        store_id="store-001",
        camera_id="cam-1",
        occurred_at=datetime.now(timezone.utc),
        confidence=0.9,
        track_id=1,
        frame_index=10,
    )
    defaults.update(kwargs)
    return build_event(EventBuildParams(**defaults))


def test_build_event_deterministic_id():
    params = EventBuildParams(
        event_type=EventType.ENTRY,
        store_id="s",
        camera_id="c",
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        confidence=0.8,
        track_id=5,
        frame_index=100,
        idempotency_subtype="line-a",
    )
    e1 = build_event(params)
    e2 = build_event(params)
    assert e1.event_id == e2.event_id


def test_metadata_in_payload():
    ev = build_event(
        EventBuildParams(
            event_type=EventType.ZONE_ENTER,
            store_id="s",
            camera_id="c",
            occurred_at=datetime.now(timezone.utc),
            confidence=0.7,
            track_id=1,
            frame_index=1,
            payload={"zone_id": "lobby"},
            metadata=EventMetadata(source="zones", pipeline_run_id="run-1"),
        )
    )
    assert ev.payload["_meta"]["source"] == "zones"
    assert ev.payload["zone_id"] == "lobby"


def test_timestamp_for_frame():
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    ts = timestamp_for_frame(30, fps=30.0, started_at=base)
    assert ts == base.replace(second=1)


def test_jsonl_roundtrip():
    ev = _sample_event()
    line = event_to_jsonl_line(ev)
    parsed = parse_jsonl_line(line)
    assert parsed.event_id == ev.event_id
    assert parsed.event_type == ev.event_type


def test_emitter_append_only(tmp_path: Path):
    settings = EmitSettings(
        output_dir=tmp_path,
        store_id="store-001",
        camera_id="cam-1",
        batch_size=2,
        dedup_in_memory=True,
    )
    emitter = StructuredEventEmitter(settings)
    e1 = _sample_event(frame_index=1)
    e2 = _sample_event(frame_index=2, event_type=EventType.EXIT)
    assert emitter.emit(e1)
    assert emitter.emit(e2)
    stats = emitter.close()
    assert stats is not None
    assert stats.events_written == 2
    assert stats.path.is_file()
    content = stats.path.read_text(encoding="utf-8")
    assert content.count("\n") >= 2


def test_emitter_dedup_in_memory():
    emitter = StructuredEventEmitter(
        EmitSettings(output_dir=Path("data/events"), dedup_in_memory=True)
    )
    ev = _sample_event()
    assert emitter.emit(ev) is True
    assert emitter.emit(ev) is False
    assert emitter.stats["total_duplicates"] == 1


def test_filter_duplicates_list():
    e1 = _sample_event(frame_index=1)
    e2 = _sample_event(frame_index=2)
    unique, dups = filter_duplicates([e1, e1, e2])
    assert len(unique) == 2
    assert dups == 1


def test_replay_load(tmp_path: Path):
    log = AppendOnlyEventLog(tmp_path, store_id="s", camera_id="c")
    path = log.path_for(datetime(2026, 5, 1, tzinfo=timezone.utc))
    ev = _sample_event()
    log.append_lines(path, [event_to_jsonl_line(ev)])
    loaded = load_log_events(path)
    assert len(loaded) == 1
    assert loaded[0].event_id == ev.event_id


def test_validate_rejects_bad_confidence():
    ev = _sample_event()
    raw = ev.model_dump()
    raw["confidence"] = 1.5
    with pytest.raises(Exception):
        EventEnvelope.model_validate(raw)


def test_event_dedup_store_eviction():
    store = EventDedupStore(max_keys=2)
    assert store.add(UUID(int=1))
    assert store.add(UUID(int=2))
    assert store.add(UUID(int=3))
    assert len(store) <= 2
