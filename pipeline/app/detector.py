"""YOLOv8 person detector wrapper."""

from __future__ import annotations

import numpy as np

from pipeline.app.interfaces import Detection, IDetector
from pipeline.settings import PipelineSettings
from shared.logging import get_logger

logger = get_logger(__name__)


class YoloDetector(IDetector):
    def __init__(self, settings: PipelineSettings) -> None:
        self._settings = settings
        self._model = None  # TODO: ultralytics.YOLO

    def _load_model(self) -> None:
        if self._model is not None:
            return
        logger.info("loading_yolo_model")
        # from ultralytics import YOLO
        # self._model = YOLO("yolov8n.pt")

    def detect(self, frame: np.ndarray) -> list[Detection]:
        self._load_model()
        _ = frame
        return []
