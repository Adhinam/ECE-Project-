# PROMPT:
# Pipeline detection config resolution tests.
#
# CHANGES MADE:
# - models.yaml path and YOLO threshold loading

"""Tests for pipeline.config."""

from pathlib import Path

import pytest

from pipeline.config import DetectionConfig, DetectorYamlConfig


def test_detector_yaml_from_models_config():
    root = Path(__file__).resolve().parents[2]
    cfg = DetectionConfig(models_config_path=root / "configs" / "models.yaml")
    yaml_cfg = cfg.load_detector_yaml()
    assert yaml_cfg.model_name == "yolov8n.pt"
    assert yaml_cfg.person_class_id == 0
    assert yaml_cfg.confidence_threshold == 0.5


def test_resolved_thresholds_env_override():
    root = Path(__file__).resolve().parents[2]
    cfg = DetectionConfig(
        models_config_path=root / "configs" / "models.yaml",
        confidence_threshold=0.7,
        iou_threshold=0.4,
    )
    assert cfg.resolved_confidence() == 0.7
    assert cfg.resolved_iou() == 0.4


def test_missing_models_yaml_raises(tmp_path: Path):
    cfg = DetectionConfig(models_config_path=tmp_path / "missing.yaml")
    with pytest.raises(FileNotFoundError):
        cfg.load_detector_yaml()
