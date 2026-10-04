import numpy as np
import pytest
import supervision as sv

from pipeline.config import TrackerYamlConfig
from pipeline.deep_ocsort import DeepOCSortAdapter
from pipeline.identity import IdentityMemory
from pipeline.tracker import TrackedVisitor


def test_real_backend_uses_appearance_and_recovers_after_three_seconds():
    adapter = DeepOCSortAdapter(TrackerYamlConfig(tracker_type="deepocsort"), 13)
    calls = []

    def encoder(image, boxes):
        calls.append(len(boxes))
        return [np.array([1., 0.], dtype=np.float32) for _ in boxes]

    adapter.backend.encoder = encoder
    image = np.zeros((400, 600, 3), dtype=np.uint8)
    det = sv.Detections(xyxy=np.array([[50, 40, 100, 200]], dtype=np.float32),
                        confidence=np.array([.9]), class_id=np.array([0]))
    first = adapter.update_with_detections(det, image).tracker_id[0]
    for _ in range(3):
        adapter.update_with_detections(det, image)
    for _ in range(39):
        adapter.update_with_detections(sv.Detections.empty(), image)
    recovered = adapter.update_with_detections(det, image)
    assert recovered.tracker_id.tolist() == [first]
    assert calls
    assert adapter.backend.args.with_reid is True
    assert adapter.backend.max_frames_lost == 130


def test_session_memory_does_not_expire_unresolved_visit():
    cfg = TrackerYamlConfig(identity_memory_seconds=None, identity_confirm_frames=1)
    m = IdentityMemory(cfg, 10, encoder=lambda crops: np.array([[1., 0.] for _ in crops]))
    image = np.zeros((400, 600, 3), dtype=np.uint8)
    v = TrackedVisitor(1, (50, 20, 90, 140), .9, (70, 80))
    m.update([v], image, 0)
    m.update([], image, 36000)
    assert m.people[1].state == "hidden"
    assert m.people[1].samples


def test_capacity_does_not_silently_forget_unresolved_visit():
    cfg = TrackerYamlConfig(identity_memory_seconds=None, identity_max_entries=1,
                            identity_confirm_frames=1)
    m = IdentityMemory(cfg, 10, encoder=lambda crops: np.array([[1., 0.] for _ in crops]))
    image = np.zeros((400, 600, 3), dtype=np.uint8)
    first = TrackedVisitor(1, (50, 20, 90, 140), .9, (70, 80))
    other = TrackedVisitor(2, (200, 20, 240, 140), .9, (220, 80))
    m.update([first], image, 0)
    with pytest.raises(RuntimeError, match="unresolved visits"):
        m.update([first, other], image, 1)
