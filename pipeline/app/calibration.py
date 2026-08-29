"""Confidence calibration — map raw scores to calibrated [0,1]."""

from __future__ import annotations

import yaml

from pipeline.settings import PipelineSettings


class ConfidenceCalibrator:
    def __init__(self, settings: PipelineSettings) -> None:
        self._settings = settings
        self._thresholds: dict[str, float] = {}

    def load(self) -> None:
        with open(self._settings.models_config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self._thresholds = data.get("calibration", {}).get("thresholds", {})

    def calibrate(self, event_type: str, raw_confidence: float) -> float:
        threshold = self._thresholds.get(event_type, 0.5)
        return min(1.0, max(0.0, raw_confidence * threshold))
