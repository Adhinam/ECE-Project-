"""Bounded, camera-local appearance memory; a missing detection is never an exit."""

from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np


class AppearanceEncoder:
    """Official YOLO26 ReID ONNX encoder, RGB / 255 at 224px as in Ultralytics."""

    def __init__(self, model):
        import onnxruntime as ort
        from ultralytics.utils.downloads import attempt_download_asset

        path = str(Path(model)) if Path(model).is_file() else attempt_download_asset(model)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(path, options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def __call__(self, crops):
        batch = np.stack([
            cv2.resize(c, (224, 224), interpolation=cv2.INTER_LINEAR)[:, :, ::-1]
            .transpose(2, 0, 1).astype(np.float32) / 255 for c in crops
        ])
        features = self.session.run(None, {self.input_name: batch})[0]
        return features / np.maximum(np.linalg.norm(features, axis=1, keepdims=True), 1e-12)


@dataclass
class Identity:
    person_id: int
    last_seen: float
    centroid: tuple
    samples: deque = field(default_factory=lambda: deque(maxlen=8))
    last_sample: float = -1e9
    state: str = "visible"


class IdentityMemory:
    """Recover fragmented raw tracks using appearance, position and ambiguity checks.

    IDs are local to one video/camera run, not verified customer identities. Expiry
    forgets an unresolved person without inventing a departure event.
    """

    def __init__(self, config, fps, encoder=None):
        self.config, self.fps, self.encoder = config, fps, encoder
        self.people = {}
        self.bindings = {}
        self.next_id = 1
        self.events = []
        self.raw_ids = set()
        self.candidate_log = []
        self.pending = {}

    def mark_exited(self, person_id):
        """Call only after a calibrated outward crossing, never a track timeout."""
        if person_id in self.people:
            self.people[person_id].state = "exited"

    def update(self, visitors, image, frame_index):
        now = frame_index / self.fps
        for pid, person in list(self.people.items()):
            if (self.config.identity_memory_seconds is not None and
                now - person.last_seen > self.config.identity_memory_seconds):
                del self.people[pid]
            elif person.state == "visible":
                person.state = "hidden"
        self.bindings = {r: p for r, p in self.bindings.items() if p in self.people}
        # Reserve every existing identity before matching any newcomers, irrespective of order.
        reserved = {self.bindings[v.track_id] for v in visitors if v.track_id in self.bindings}
        features, selected, crops = {}, [], []
        height, width = image.shape[:2]
        diagonal = float(np.hypot(width, height))
        for v in visitors:
            self.raw_ids.add(v.track_id)
            p = self.people.get(self.bindings.get(v.track_id))
            if p and now - p.last_sample < self.config.identity_sample_seconds:
                continue
            x1, y1, x2, y2 = v.bbox_xyxy
            x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
            if v.confidence < 0.4 or x2 - x1 < 24 or y2 - y1 < 60:
                continue
            # Reject very flat head-only or shelf-dominated crops at the image edge.
            if (y2 - y1) / (x2 - x1) < 0.8:
                continue
            area = (x2 - x1) * (y2 - y1)
            contaminated = False
            for other in visitors:
                if other.track_id == v.track_id:
                    continue
                a, b, c, d = other.bbox_xyxy
                overlap = max(0, min(x2, c) - max(x1, a)) * max(0, min(y2, d) - max(y1, b))
                if overlap / area > 0.25:
                    contaminated = True
                    break
            if not contaminated:
                crops.append(image[y1:y2, x1:x2])
                selected.append(v.track_id)
        if crops:
            if self.encoder is None:
                self.encoder = AppearanceEncoder(self.config.identity_model)
            features = dict(zip(selected, self.encoder(crops)))

        visible_raw = {v.track_id for v in visitors}
        self.pending = {r: n for r, n in self.pending.items() if r in visible_raw}
        for v in visitors:
            if v.track_id not in self.bindings:
                self.pending[v.track_id] = self.pending.get(v.track_id, 0) + 1
        visitors = [v for v in visitors if v.track_id in self.bindings or
                    (self.pending[v.track_id] >= self.config.identity_confirm_frames and
                     (v.track_id in features or self.pending[v.track_id] >= max(self.config.identity_confirm_frames, int(self.fps))))]
        # Score newcomers against the same gallery snapshot, then require mutual best matches.
        choices = {}
        for v in visitors:
            if v.track_id in self.bindings or v.track_id not in features:
                continue
            candidates = []
            for pid, person in self.people.items():
                gap = now - person.last_seen
                if pid in reserved or person.state == "exited" or not person.samples or gap <= 0:
                    continue
                distance = np.linalg.norm(np.subtract(v.centroid, person.centroid)) / diagonal
                if distance > min(0.65, 0.08 + 0.06 * gap):
                    continue
                score = max(float(np.dot(features[v.track_id], old)) for old in person.samples)
                candidates.append((score, pid))
            candidates.sort(reverse=True)
            if candidates:
                self.candidate_log.append(dict(frame_index=frame_index, raw_track_id=v.track_id,
                    candidates=candidates[:3]))
            if candidates and candidates[0][0] >= self.config.identity_similarity:
                runner_up = candidates[1][0] if len(candidates) > 1 else -1
                if candidates[0][0] - runner_up >= self.config.identity_margin:
                    choices[v.track_id] = candidates[0]
        output = []
        for v in visitors:
            raw = v.track_id
            if raw not in self.bindings:
                candidate = choices.get(raw)
                competitors = sorted([s for r, (s, pid) in choices.items()
                                      if r != raw and candidate and pid == candidate[1]], reverse=True)
                if (candidate and candidate[1] not in reserved and
                    (not competitors or candidate[0] - competitors[0] >= self.config.identity_margin)):
                    score, pid = candidate
                    person = self.people[pid]
                    self.events.append(dict(frame_index=frame_index, raw_track_id=raw,
                        person_id=pid, action="recovered", similarity=score,
                        gap_seconds=now - person.last_seen))
                    # Retire the previous raw binding so it cannot duplicate this identity later.
                    self.bindings = {r: p for r, p in self.bindings.items() if p != pid}
                else:
                    if self.config.identity_memory_seconds is None and len(self.people) >= self.config.identity_max_entries:
                        exited = [p for p in self.people.values() if p.state == "exited" and p.person_id not in reserved]
                        if not exited:
                            raise RuntimeError("Identity capacity reached: unresolved visits will not be silently forgotten")
                        del self.people[min(exited, key=lambda p: p.last_seen).person_id]
                    pid = self.next_id
                    self.next_id += 1
                    self.people[pid] = Identity(pid, now, v.centroid)
                    self.events.append(dict(frame_index=frame_index, raw_track_id=raw,
                                            person_id=pid, action="new"))
                self.bindings[raw] = pid
                reserved.add(pid)
            pid = self.bindings[raw]
            person = self.people[pid]
            person.last_seen, person.centroid = now, v.centroid
            if person.state != "exited":
                person.state = "visible"
            if raw in features and person.state != "exited":
                person.samples.append(features[raw])
                person.last_sample = now
            output.append(replace(v, track_id=pid, raw_track_id=raw))
        # Hard bound for long live runs. Visible identities cannot be evicted mid-frame.
        excess = max(0, len(self.people) - self.config.identity_max_entries)
        hidden = sorted((p for p in self.people.values() if p.person_id not in reserved and
                         (self.config.identity_memory_seconds is not None or p.state == "exited")),
                        key=lambda p: p.last_seen)
        for p in hidden[:excess]:
            del self.people[p.person_id]
        return output
