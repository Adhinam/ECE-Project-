# PROMPT:
# Zone enter/exit/dwell engine unit tests.
#
# CHANGES MADE:
# - Dwell threshold and occlusion grace coverage

"""Unit tests for zone tracking engine."""

from pipeline.entry_exit import VideoClock
from pipeline.tracker import FrameTracks, TrackedVisitor
from pipeline.zones import (
    ZoneSettings,
    ZoneTrackingEngine,
    _polygon_from_config,
    load_layout_file,
    load_zone_polygons,
    zones_for_point,
)
from schemas.config import StoreLayoutConfig, ZoneConfig
from schemas.events import EventType


def _layout() -> StoreLayoutConfig:
    return StoreLayoutConfig(
        store_id="store-001",
        dwell_threshold_seconds=30,
        zones=[
            ZoneConfig(
                id="lobby",
                polygon=[[0, 0], [100, 0], [100, 100], [0, 100]],
            ),
            ZoneConfig(
                id="inner",
                polygon=[[20, 20], [80, 20], [80, 80], [20, 80]],
            ),
        ],
    )


def test_overlapping_zones_both_match():
    layout = _layout()
    zones = load_zone_polygons(layout)
    matched = zones_for_point(50.0, 50.0, zones)
    assert "lobby" in matched
    assert "inner" in matched


def test_zone_enter_and_exit():
    layout = _layout()
    settings = ZoneSettings(
        dwell_threshold_seconds=1000,
        dwell_emit_interval_seconds=1000,
        occlusion_grace_frames=0,
    )
    engine = ZoneTrackingEngine(
        layout,
        settings,
        camera_id="cam-1",
        clock=VideoClock(fps=30.0),
    )
    inside = FrameTracks(
        frame_index=1,
        tracks=(
            TrackedVisitor(1, (40, 40, 60, 60), 0.9, (50.0, 50.0)),
        ),
    )
    events_in = engine.process_frame(inside)
    assert any(e.event_type == EventType.ZONE_ENTER for e in events_in)

    outside = FrameTracks(
        frame_index=2,
        tracks=(
            TrackedVisitor(1, (150, 150, 170, 170), 0.9, (160.0, 160.0)),
        ),
    )
    events_out = engine.process_frame(outside)
    assert any(e.event_type == EventType.ZONE_EXIT for e in events_out)


def test_occlusion_grace_delays_exit():
    layout = StoreLayoutConfig(
        store_id="s",
        zones=[ZoneConfig(id="z", polygon=[[0, 0], [100, 0], [100, 100], [0, 100]])],
    )
    settings = ZoneSettings(
        dwell_threshold_seconds=999,
        occlusion_grace_frames=3,
    )
    engine = ZoneTrackingEngine(
        layout,
        settings,
        camera_id="cam",
        clock=VideoClock(fps=30.0),
    )
    engine.process_frame(
        FrameTracks(1, (TrackedVisitor(5, (40, 40, 60, 60), 0.9, (50.0, 50.0)),))
    )
    for idx in range(2, 4):
        events = engine.process_frame(FrameTracks(idx, tracks=()))
        assert not any(e.event_type == EventType.ZONE_EXIT for e in events)
    events = engine.process_frame(FrameTracks(4, tracks=()))
    assert any(e.event_type == EventType.ZONE_EXIT for e in events)


def test_dwell_emitted_after_threshold():
    layout = StoreLayoutConfig(
        store_id="s",
        zones=[ZoneConfig(id="z", polygon=[[0, 0], [100, 0], [100, 100], [0, 100]])],
    )
    settings = ZoneSettings(
        dwell_threshold_seconds=1.0,
        dwell_emit_interval_seconds=30.0,
        occlusion_grace_frames=5,
    )
    clock = VideoClock(fps=30.0)
    engine = ZoneTrackingEngine(layout, settings, camera_id="cam", clock=clock)

    visitor = TrackedVisitor(1, (40, 40, 60, 60), 0.88, (50.0, 50.0))
    engine.process_frame(FrameTracks(0, (visitor,)))
    for idx in range(1, 35):
        events = engine.process_frame(FrameTracks(idx, (visitor,)))
    dwells = [e for e in events if e.event_type == EventType.ZONE_DWELL]
    assert len(dwells) >= 1
    assert dwells[0].payload["session_sequence"] == 1


def test_session_sequence_increments_on_reenter():
    layout = StoreLayoutConfig(
        store_id="s",
        zones=[ZoneConfig(id="z", polygon=[[0, 0], [100, 0], [100, 100], [0, 100]])],
    )
    settings = ZoneSettings(dwell_threshold_seconds=999, occlusion_grace_frames=0)
    engine = ZoneTrackingEngine(
        layout, settings, camera_id="cam", clock=VideoClock(fps=30.0)
    )
    inside = TrackedVisitor(1, (40, 40, 60, 60), 0.9, (50.0, 50.0))
    outside = TrackedVisitor(1, (200, 200, 220, 220), 0.9, (210.0, 210.0))
    engine.process_frame(FrameTracks(1, (inside,)))
    engine.process_frame(FrameTracks(2, (outside,)))
    engine.process_frame(FrameTracks(3, (inside,)))
    history = engine.histories[1]
    sequences = [v.session_sequence for v in history.visits if v.zone_id == "z"]
    assert sequences == [1, 2]


def test_load_layout_json(tmp_path):
    path = tmp_path / "store_layout.json"
    path.write_text(
        '{"store_id":"x","zones":[{"id":"a","polygon":[[0,0],[10,0],[10,10],[0,10]]}]}',
        encoding="utf-8",
    )
    layout = load_layout_file(path)
    assert layout.store_id == "x"
    assert len(layout.zones) == 1


def test_invalid_polygon_raises():
    import pytest

    with pytest.raises(ValueError):
        _polygon_from_config(ZoneConfig(id="bad", polygon=[[0, 0], [1, 1]]))
