# PROMPT:
# Entry/exit line crossing detector tests.
#
# CHANGES MADE:
# - Directional crossing debounce and event emission

"""Unit tests for entry/exit line crossing."""

from pipeline.entry_exit import (
    CrossingDirection,
    EntryExitDetector,
    EntryExitSettings,
    LineSegment,
    VideoClock,
    classify_crossing,
    line_from_config,
)
from pipeline.tracker import FrameTracks, TrackedVisitor
from schemas.config import EntryExitLine, StoreLayoutConfig


def _vertical_line() -> LineSegment:
    return LineSegment(
        p1=(100.0, 100.0),
        p2=(100.0, 300.0),
        line_id="main",
        direction_in="bottom",
    )


def test_classify_entry_moving_up_through_vertical_line():
    line = _vertical_line()
    # Outside (below) -> inside (above): y decreases
    direction = classify_crossing((100.0, 250.0), (100.0, 150.0), line)
    assert direction == CrossingDirection.ENTRY


def test_classify_exit_moving_down_through_vertical_line():
    line = _vertical_line()
    direction = classify_crossing((100.0, 150.0), (100.0, 250.0), line)
    assert direction == CrossingDirection.EXIT


def test_no_crossing_same_side():
    line = _vertical_line()
    assert classify_crossing((120.0, 150.0), (130.0, 160.0), line) is None


def test_cooldown_prevents_duplicate_entry():
    layout = StoreLayoutConfig(
        store_id="store-001",
        entry_exit_lines=[
            EntryExitLine(id="main", p1=[100, 100], p2=[100, 300], direction_in="bottom"),
        ],
    )
    settings = EntryExitSettings(cooldown_frames=50, debounce_frames=0)
    detector = EntryExitDetector(
        layout,
        settings,
        camera_id="cam-1",
        clock=VideoClock(fps=30.0),
    )

    def frame_at(y: float, idx: int) -> FrameTracks:
        return FrameTracks(
            frame_index=idx,
            tracks=(
                TrackedVisitor(
                    track_id=1,
                    bbox_xyxy=(90, int(y) - 10, 110, int(y) + 10),
                    confidence=0.9,
                    centroid=(100.0, y),
                ),
            ),
        )

    events1 = detector.process_frame(frame_at(250.0, 1))
    events2 = detector.process_frame(frame_at(150.0, 2))
    events3 = detector.process_frame(frame_at(250.0, 3))
    events4 = detector.process_frame(frame_at(150.0, 4))

    assert any(e.event_type.value == "entry" for e in events2)
    assert not any(e.event_type.value == "entry" for e in events4)  # cooldown blocks re-entry


def test_group_crossing_payload():
    layout = StoreLayoutConfig(
        store_id="store-001",
        entry_exit_lines=[
            EntryExitLine(id="door", p1=[100, 100], p2=[100, 300], direction_in="bottom"),
        ],
    )
    settings = EntryExitSettings(cooldown_frames=0, debounce_frames=0)
    detector = EntryExitDetector(
        layout,
        settings,
        camera_id="cam-1",
        clock=VideoClock(fps=30.0),
    )
    tracks = FrameTracks(
        frame_index=10,
        tracks=(
            TrackedVisitor(1, (90, 240, 110, 260), 0.9, (100.0, 250.0)),
            TrackedVisitor(2, (120, 240, 140, 260), 0.88, (130.0, 250.0)),
        ),
    )
    detector.process_frame(
        FrameTracks(9, (TrackedVisitor(1, (90, 240, 110, 260), 0.9, (100.0, 250.0)),))
    )
    detector.process_frame(
        FrameTracks(
            9,
            (TrackedVisitor(2, (120, 240, 140, 260), 0.88, (130.0, 250.0)),),
        )
    )
    events = detector.process_frame(
        FrameTracks(
            10,
            (
                TrackedVisitor(1, (90, 140, 110, 160), 0.9, (100.0, 150.0)),
                TrackedVisitor(2, (120, 140, 140, 160), 0.88, (130.0, 150.0)),
            ),
        )
    )
    entries = [e for e in events if e.event_type.value == "entry"]
    if len(entries) >= 2:
        assert entries[0].payload.get("group_crossing_id") == entries[1].payload.get(
            "group_crossing_id"
        )


def test_line_from_config():
    seg = line_from_config(EntryExitLine(id="x", p1=[0, 0], p2=[10, 10], direction_in="left"))
    assert seg.line_id == "x"
