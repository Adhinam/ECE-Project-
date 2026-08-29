# PROMPT:
# Track history store and group-entry candidate tests.
#
# CHANGES MADE:
# - Movement history ring buffer behavior

"""Unit tests for track_state."""

from pipeline.track_state import (
    TrackHistoryStore,
    TrackPoint,
    TrackStatus,
    bbox_centroid,
)


def test_bbox_centroid():
    c = bbox_centroid((0, 0, 100, 200))
    assert c.x == 50.0
    assert c.y == 100.0


def test_track_lifecycle_timeout():
    store = TrackHistoryStore(track_timeout_frames=1, max_history_points=100)
    point = TrackPoint(
        frame_index=0,
        centroid=bbox_centroid((10, 10, 30, 30)),
        bbox_xyxy=(10, 10, 30, 30),
        confidence=0.9,
    )
    store.update_frame(0, [point], track_ids=[1])
    assert store.get(1) is not None
    assert store.get(1).status == TrackStatus.ACTIVE

    # Missing → LOST, then ENDED after timeout_frames exceeded
    store.update_frame(1, [], track_ids=[])
    assert store.get(1).status == TrackStatus.LOST
    store.update_frame(2, [], track_ids=[])
    assert store.get(1).status == TrackStatus.ENDED


def test_occlusion_recovery_reactivates():
    store = TrackHistoryStore(track_timeout_frames=10, max_history_points=100)
    p0 = TrackPoint(0, bbox_centroid((0, 0, 10, 10)), (0, 0, 10, 10), 0.9)
    store.update_frame(0, [p0], track_ids=[7])
    store.update_frame(1, [], track_ids=[])
    assert store.get(7).status == TrackStatus.LOST

    p1 = TrackPoint(2, bbox_centroid((5, 5, 15, 15)), (5, 5, 15, 15), 0.85)
    store.update_frame(2, [p1], track_ids=[7])
    assert store.get(7).status == TrackStatus.ACTIVE
    assert len(store.get(7).points) == 2


def test_group_entry_detection():
    store = TrackHistoryStore(
        track_timeout_frames=30,
        group_entry_min_size=2,
        group_entry_frame_window=0,
    )
    p1 = TrackPoint(10, bbox_centroid((0, 0, 10, 10)), (0, 0, 10, 10), 0.9)
    p2 = TrackPoint(10, bbox_centroid((20, 0, 30, 10)), (20, 0, 30, 10), 0.9)
    store.update_frame(10, [p1, p2], track_ids=[1, 2])
    groups = store.detect_group_entries(10)
    assert len(groups) == 1
    assert groups[0].group_size == 2


def test_movement_history_distance():
    store = TrackHistoryStore(track_timeout_frames=30)
    points = [
        TrackPoint(i, bbox_centroid((i * 10, 0, i * 10 + 10, 10)), (i * 10, 0, i * 10 + 10, 10), 0.9)
        for i in range(3)
    ]
    store.update_frame(0, [points[0]], track_ids=[1])
    store.update_frame(1, [points[1]], track_ids=[1])
    store.update_frame(2, [points[2]], track_ids=[1])
    hist = store.get(1).movement_history()
    assert hist.total_distance_px > 0
    assert len(hist.samples) == 3
