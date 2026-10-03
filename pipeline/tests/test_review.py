"""Exercise video review without downloading weights or requiring a GPU."""

import csv
import json
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest

from pipeline.detect import FrameDetections, PersonDetector
from pipeline.review import review_video, summarize_timings


def test_timing_uses_processed_frames():
    assert summarize_timings([100, 200])["fps"] == pytest.approx(1000 / 150)
    assert summarize_timings([])["fps"] is None


def test_detector_passes_settings_and_original_coordinates(monkeypatch):
    backend = MagicMock()
    backend.predict.return_value = []
    monkeypatch.setattr("pipeline.detect.YOLO", lambda _: backend)
    detector = PersonDetector(
        model_path="yolo26n.pt",
        confidence=0.1,
        iou=0.45,
        person_class_id=0,
        device="cpu",
        imgsz=480,
    )
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    result = detector.detect_frame(frame, 12)
    assert result.frame_index == 12
    assert result.detections == ()
    assert backend.predict.call_args.kwargs["imgsz"] == 480
    assert backend.predict.call_args.kwargs["classes"] == [0]
    assert backend.predict.call_args.kwargs["source"] is frame


def test_review_writes_video_csv_and_honest_report(tmp_path, monkeypatch):
    source = tmp_path / "input.mp4"
    writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*"mp4v"), 10, (64, 64))
    assert writer.isOpened()
    for _ in range(12):
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.release()
    observed = []

    class FakeDetector:
        def __init__(self, **kwargs):
            observed.append(kwargs)

        def detect_frame(self, frame, index):
            return FrameDetections(index, (), inference_ms=10)

    monkeypatch.setattr("pipeline.review.PersonDetector", FakeDetector)
    output = tmp_path / "review"
    report = review_video(source, output, max_seconds=1, imgsz=320)
    assert report["frames_processed"] == 10
    assert report["distinct_track_ids_not_unique_visitors"] == 0
    assert report["detector_timing"]["fps"] == 100
    assert observed[0]["imgsz"] == 320
    assert observed[0]["model_path"] == "yolo26n.pt"
    assert "Not measured" in report["accuracy_status"]
    assert json.loads((output / "report.json").read_text())["frames_processed"] == 10
    with (output / "frames.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert [int(row["frame_index"]) for row in rows] == list(range(10))
    cap = cv2.VideoCapture(str(output / "annotated.mp4"))
    assert cap.isOpened()
    assert cap.get(cv2.CAP_PROP_FPS) == pytest.approx(10)
    assert cap.get(cv2.CAP_PROP_FRAME_COUNT) == 10
    cap.release()
    with pytest.raises(FileExistsError):
        review_video(source, output)


def test_review_rejects_missing_video(tmp_path):
    with pytest.raises(FileNotFoundError):
        review_video(tmp_path / "missing.mp4", tmp_path / "out")
