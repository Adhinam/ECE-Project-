"""Identity recovery safeguards using deterministic appearance vectors."""

import numpy as np

from pipeline.config import TrackerYamlConfig
from pipeline.identity import IdentityMemory
from pipeline.tracker import TrackedVisitor, _create_byte_tracker


def visitor(raw, x=50, confidence=0.9):
    return TrackedVisitor(raw, (x, 20, x + 40, 140), confidence, (x + 20, 80))


def memory(**kwargs):
    return IdentityMemory(TrackerYamlConfig(identity_confirm_frames=1, **kwargs), 10,
                          encoder=lambda crops: np.array([[1., 0.] for _ in crops]))


IMAGE = np.zeros((400, 600, 3), dtype=np.uint8)


def test_recovers_after_occlusion_and_retains_raw_audit():
    m = memory()
    first = m.update([visitor(1)], IMAGE, 0)[0]
    m.update([], IMAGE, 10)
    assert m.people[first.track_id].state == "hidden"
    recovered = m.update([visitor(9, 70)], IMAGE, 20)[0]
    assert recovered.track_id == first.track_id
    assert recovered.raw_track_id == 9
    assert m.events[-1]["action"] == "recovered"
    assert m.events[-1]["gap_seconds"] == 2


def test_visible_person_reserved_before_newcomer_matching():
    m = memory()
    m.update([visitor(1)], IMAGE, 0)
    tracks = m.update([visitor(9, 150), visitor(1)], IMAGE, 30)
    assert len({t.track_id for t in tracks}) == 2
    assert tracks[1].track_id == 1


def test_ambiguous_similar_people_are_not_merged():
    m = memory()
    m.update([visitor(1), visitor(2, 150)], IMAGE, 0)
    tracks = m.update([visitor(9, 100)], IMAGE, 30)
    assert tracks[0].track_id == 3


def test_two_newcomers_competing_for_same_person_are_not_merged():
    m = memory()
    m.update([visitor(1)], IMAGE, 0)
    tracks = m.update([visitor(8, 50), visitor(9, 150)], IMAGE, 30)
    assert len({t.track_id for t in tracks}) == 2
    assert all(t.track_id != 1 for t in tracks)


def test_weak_detection_never_updates_appearance():
    m = memory()
    m.update([visitor(1, confidence=0.2)], IMAGE, 0)
    assert not m.people


def test_overlapping_crops_do_not_poison_gallery():
    m = memory()
    m.update([visitor(1), visitor(2, 55)], IMAGE, 0)
    assert not m.people


def test_expiry_is_not_a_confirmed_exit():
    m = memory(identity_memory_seconds=2)
    m.update([visitor(1)], IMAGE, 0)
    m.update([], IMAGE, 30)
    assert not m.people
    assert all(e["action"] != "exit" for e in m.events)
    assert m.update([visitor(9)], IMAGE, 31)[0].track_id == 2


def test_confirmed_exit_excluded_from_occlusion_recovery():
    m = memory()
    m.update([visitor(1)], IMAGE, 0)
    m.mark_exited(1)
    assert m.update([visitor(9)], IMAGE, 20)[0].track_id == 2


def test_impossible_short_gap_motion_rejected():
    m = memory()
    m.update([visitor(1)], IMAGE, 0)
    assert m.update([visitor(9, 500)], IMAGE, 1)[0].track_id == 2


def test_buffer_means_four_seconds_at_13fps_and_separate_new_threshold():
    tracker = _create_byte_tracker(TrackerYamlConfig(
        track_thresh=0.3, new_track_thresh=0.5, track_buffer=120), frame_rate=13)
    assert tracker.max_time_lost == 52
    assert tracker.det_thresh == 0.5


def test_tentative_track_requires_consecutive_observations():
    m = IdentityMemory(TrackerYamlConfig(identity_confirm_frames=3), 10,
                       encoder=lambda crops: np.array([[1., 0.] for _ in crops]))
    assert not m.update([visitor(1)], IMAGE, 0)
    assert not m.update([visitor(1)], IMAGE, 1)
    assert m.update([visitor(1)], IMAGE, 2)[0].track_id == 1
    assert not m.update([visitor(8, 200)], IMAGE, 3)
    m.update([], IMAGE, 4)
    assert not m.update([visitor(8, 200)], IMAGE, 5)


def test_flat_head_only_crop_is_not_identity_evidence():
    m = memory()
    v = TrackedVisitor(1, (50, 20, 250, 90), .9, (150,55))
    assert not m.update([v], IMAGE, 0)
    assert not m.people
