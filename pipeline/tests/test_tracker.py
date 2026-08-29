# PROMPT:
# ByteTrack visitor tracker conversion helper tests.
#
# CHANGES MADE:
# - supervision Detections → FrameTracks without full YOLO run

"""Unit tests for tracker conversion helpers (no YOLO/ByteTrack required)."""

import numpy as np
import supervision as sv

from pipeline.detect import FrameDetections, PersonDetection
from pipeline.tracker import (
    detections_to_supervision,
    supervision_to_tracked_visitors,
)


def test_detections_to_supervision_empty():
    frame = FrameDetections(frame_index=0, detections=())
    sv_det = detections_to_supervision(frame)
    assert sv_det.is_empty()


def test_detections_to_supervision_and_back():
    det = PersonDetection(
        bbox_xyxy=(10, 20, 50, 80),
        confidence=0.92,
        class_id=0,
        class_name="person",
    )
    frame = FrameDetections(frame_index=5, detections=(det,))
    sv_det = detections_to_supervision(frame)
    assert len(sv_det) == 1
    assert float(sv_det.confidence[0]) == 0.92

    # Simulate ByteTrack output with assigned ID
    tracked = sv.Detections(
        xyxy=sv_det.xyxy,
        confidence=sv_det.confidence,
        class_id=sv_det.class_id,
        tracker_id=np.array([42]),
    )
    visitors = supervision_to_tracked_visitors(tracked, frame_index=5)
    assert len(visitors) == 1
    assert visitors[0].track_id == 42
    assert visitors[0].centroid == (30.0, 50.0)
