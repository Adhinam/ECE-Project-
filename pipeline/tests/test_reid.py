# PROMPT:
# Lightweight Re-ID coordinator tests (histogram gallery).
#
# CHANGES MADE:
# - Reentry match and gallery eviction behavior

"""Unit tests for lightweight Re-ID."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import numpy as np

from pipeline.reid import LightweightReID, ReIDSettings, ReentryCoordinator
from pipeline.entry_exit import VideoClock
from schemas.events import EventEnvelope, EventType, generate_event_id


def _solid_crop(bgr: tuple[int, int, int], size: int = 80) -> np.ndarray:
    patch = np.zeros((size, size, 3), dtype=np.uint8)
    patch[:, :] = bgr
    return patch


def _frame_with_patch(bgr: tuple[int, int, int]) -> np.ndarray:
    frame = np.zeros((200, 200, 3), dtype=np.uint8)
    frame[60:140, 60:140] = bgr
    return frame


def test_identical_features_high_similarity():
    reid = LightweightReID(ReIDSettings(enabled=True))
    frame = _frame_with_patch((0, 128, 200))
    bbox = (60, 60, 140, 140)
    f1 = reid.extract_feature(frame, bbox)
    f2 = reid.extract_feature(frame, bbox)
    assert f1 is not None and f2 is not None
    assert LightweightReID.histogram_similarity(f1, f2) > 0.99
    assert LightweightReID.cosine_similarity(f1, f2) > 0.99


def test_different_colors_low_similarity():
    reid = LightweightReID(ReIDSettings(enabled=True, similarity_threshold=0.9))
    f_red = reid.extract_feature(_solid_crop((0, 0, 255)), (0, 0, 80, 80))
    f_blue = reid.extract_feature(_solid_crop((255, 0, 0)), (0, 0, 80, 80))
    assert f_red and f_blue
    assert reid.combined_score(
        LightweightReID.histogram_similarity(f_red, f_blue),
        LightweightReID.cosine_similarity(f_red, f_blue),
    ) < 0.9


def test_reentry_match_within_timeout():
    settings = ReIDSettings(
        enabled=True,
        similarity_threshold=0.5,
        reentry_timeout_seconds=300,
    )
    reid = LightweightReID(settings)
    vid = uuid4()
    frame = _frame_with_patch((40, 120, 200))
    bbox = (60, 60, 140, 140)
    now = datetime.now(timezone.utc)
    reid.register_exit(
        visitor_id=vid,
        frame=frame,
        bbox_xyxy=bbox,
        exited_at=now - timedelta(seconds=60),
    )
    match = reid.match_reentry(frame=frame, bbox_xyxy=bbox, occurred_at=now)
    assert match is not None
    assert match.visitor_id == vid
    assert match.confidence >= 0.5


def test_reentry_expired_after_timeout():
    settings = ReIDSettings(enabled=True, similarity_threshold=0.5, reentry_timeout_seconds=30)
    reid = LightweightReID(settings)
    vid = uuid4()
    frame = _frame_with_patch((40, 120, 200))
    bbox = (60, 60, 140, 140)
    exited = datetime.now(timezone.utc) - timedelta(seconds=120)
    reid.register_exit(visitor_id=vid, frame=frame, bbox_xyxy=bbox, exited_at=exited)
    match = reid.match_reentry(
        frame=frame,
        bbox_xyxy=bbox,
        occurred_at=datetime.now(timezone.utc),
    )
    assert match is None


def test_degraded_when_disabled():
    reid = LightweightReID(ReIDSettings(enabled=False))
    assert reid.is_degraded
    assert reid.extract_feature(_frame_with_patch((1, 2, 3)), (0, 0, 50, 50)) is None


def test_reentry_coordinator_emits_event():
    clock = VideoClock(fps=30.0)
    coord = ReentryCoordinator(
        store_id="store-001",
        camera_id="cam-1",
        clock=clock,
        settings=ReIDSettings(enabled=True, similarity_threshold=0.5),
    )
    vid = uuid4()
    frame = _frame_with_patch((10, 200, 50))
    bbox = (60, 60, 140, 140)
    now = datetime.now(timezone.utc)
    exit_ev = EventEnvelope(
        event_id=generate_event_id(
            store_id="store-001",
            camera_id="cam-1",
            event_type="exit",
            track_id=1,
            frame_index=10,
        ),
        event_type=EventType.EXIT,
        store_id="store-001",
        camera_id="cam-1",
        occurred_at=now - timedelta(seconds=30),
        track_id=1,
        global_person_id=str(vid),
        confidence=0.9,
        payload={"line_id": "main"},
    )
    coord.on_visitor_exit(visitor_id=vid, frame=frame, bbox_xyxy=bbox, exit_event=exit_ev)
    reentry_ev, match = coord.check_reentry(
        frame=frame,
        bbox_xyxy=bbox,
        track_id=2,
        frame_index=100,
        occurred_at=now,
    )
    assert match is not None
    assert reentry_ev is not None
    assert reentry_ev.event_type == EventType.REENTRY
    assert reentry_ev.payload["histogram_similarity"] is not None
