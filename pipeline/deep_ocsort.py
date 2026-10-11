"""Adapter for Ultralytics Deep OC-SORT with an explicit CPU appearance encoder."""

from types import SimpleNamespace

import numpy as np
import supervision as sv
from ultralytics.engine.results import Boxes
from ultralytics.trackers.deep_oc_sort import DeepOCSORT

from pipeline.identity import AppearanceEncoder


class BalancedDeepOCSORT(DeepOCSORT):
    """Let appearance penalize a confident motion match, not only improve it.

    Upstream min fusion cannot reject an unrelated detection with excellent box
    overlap. Weak detections and missing embeddings retain upstream behavior.
    A zero weight preserves the original association for comparison.
    """

    def _fuse_appearance(self, dists, tracks, detections, iou_dists=None):
        fused = super()._fuse_appearance(dists, tracks, detections, iou_dists)
        weight = getattr(self.args, 'appearance_weight', 0)
        if not weight or self.encoder is None:
            return fused
        for i, track in enumerate(tracks):
            for j, detection in enumerate(detections):
                if track.smooth_feat is None or detection.curr_feat is None or detection.score < .6:
                    continue
                similarity = float(np.clip(np.dot(track.smooth_feat, detection.curr_feat), -1, 1))
                fused[i, j] = (1-weight)*dists[i, j] + weight*(1-similarity)
                if similarity < self.args.reject_similarity:
                    fused[i, j] = 1.0
        return fused


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
            appearance_weight=config.deep_appearance_weight,
            reject_similarity=config.deep_reject_similarity,
        )
        self.backend = BalancedDeepOCSORT(args)
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
