"""ByteTrack multi-object tracker wrapper."""

from __future__ import annotations

import numpy as np

from pipeline.app.interfaces import Detection, ITracker, Track
from pipeline.settings import PipelineSettings


class ByteTrackTracker(ITracker):
    def __init__(self, settings: PipelineSettings) -> None:
        self._settings = settings
        self._tracker = None  # TODO: supervision or boxmot ByteTrack

    def update(self, detections: list[Detection], frame: np.ndarray) -> list[Track]:
        _ = (detections, frame)
        return []
