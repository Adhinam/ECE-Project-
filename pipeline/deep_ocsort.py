"""Adapter for Ultralytics Deep OC-SORT with an explicit CPU appearance encoder."""

from types import SimpleNamespace

import numpy as np
import supervision as sv
from ultralytics.engine.results import Boxes
from ultralytics.trackers.deep_oc_sort import DeepOCSORT

from pipeline.identity import AppearanceEncoder


class CropEncoder:
    """Bridge upstream xywh detections to a thread-limited ONNX encoder."""

    def __init__(self, model):
        self.model = model
        self.backend = None

    def __call__(self, image, boxes):
        crops, indices = [], []
        result = [None] * len(boxes)
        height, width = image.shape[:2]
        for i, (x, y, w, h, *_) in enumerate(boxes):
            x1, y1 = max(0, int(x - w / 2)), max(0, int(y - h / 2))
            x2, y2 = min(width, int(x + w / 2)), min(height, int(y + h / 2))
            if x2 > x1 and y2 > y1:
                crops.append(image[y1:y2, x1:x2])
                indices.append(i)
        if crops:
            if self.backend is None:
                self.backend = AppearanceEncoder(self.model)
            for i, feature in zip(indices, self.backend(crops), strict=True):
                result[i] = feature
        return result


class DeepOCSortAdapter:
    def __init__(self, config, fps):
        # Inject our explicit encoder after construction to avoid a second ORT
        # session with uncontrolled CPU threading. Native feature auto mode is unused.
        args = SimpleNamespace(
            track_high_thresh=config.track_thresh, track_low_thresh=0.1,
            new_track_thresh=config.new_track_thresh or config.track_thresh,
            track_buffer=max(1, round(config.deep_lost_seconds * fps)),
            match_thresh=config.match_thresh, fuse_score=True,
            delta_t=3, inertia=0.2, use_byte=True, gmc_method="none",
            with_reid=False, model=config.identity_model, device="cpu",
            proximity_thresh=config.deep_proximity,
            appearance_thresh=config.deep_appearance,
            alpha_fixed_emb=0.95,
        )
        self.backend = DeepOCSORT(args)
        self.backend.encoder = CropEncoder(config.identity_model)
        self.backend.args.with_reid = True

    def update_with_detections(self, detections, image):
        array = np.column_stack((detections.xyxy, detections.confidence,
                                 detections.class_id)).astype(np.float32) if len(detections) else np.empty((0, 6), np.float32)
        result = self.backend.update(Boxes(array, image.shape[:2]), img=image)
        if not len(result):
            return sv.Detections.empty()
        return sv.Detections(xyxy=result[:, :4], tracker_id=result[:, 4].astype(int),
                             confidence=result[:, 5], class_id=result[:, 6].astype(int))
