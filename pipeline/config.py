"""Detection pipeline configuration — env vars + YAML, no hardcoded paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.settings import AppSettings


class TrackerYamlConfig(BaseSettings):
    """Subset of configs/models.yaml — tracker (ByteTrack) section."""

    model_config = SettingsConfigDict(extra="ignore")

    track_thresh: float = Field(default=0.5, ge=0.0, le=1.0)
    match_thresh: float = Field(default=0.8, ge=0.0, le=1.0)
    track_buffer: int = Field(default=30, ge=1, description="ByteTrack lost_track_buffer (frames)")
    frame_rate: int = Field(default=30, ge=1)


class DetectorYamlConfig(BaseSettings):
    """Subset of configs/models.yaml — detector section only."""

    model_config = SettingsConfigDict(extra="ignore")

    model_name: str = "yolov8n.pt"
    confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    iou_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    person_class_id: int = Field(default=0, ge=0)


class DetectionConfig(AppSettings):
    """
    Runtime configuration for person detection.

    Values load from environment variables and optional YAML overrides.
    Paths must be provided explicitly or via env — nothing is hardcoded to
    a developer machine.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
        # Env: DETECTION_VIDEO_SOURCE, or VIDEO_SOURCE via alias
        env_prefix="DETECTION_",
    )

    # Logging (from AppSettings)
    log_level: str = "INFO"
    log_json: bool = True

    # Model / detector thresholds
    model_path: Path | None = Field(
        default=None,
        validation_alias="MODEL_PATH",
        description="Path to YOLO weights; falls back to model_name from YAML",
    )
    models_config_path: Path = Field(
        default=Path("configs/models.yaml"),
        validation_alias="MODELS_CONFIG_PATH",
        description="YAML file with detector thresholds",
    )
    confidence_threshold: float | None = Field(
        default=None,
        description="Override YAML confidence_threshold",
    )
    iou_threshold: float | None = Field(
        default=None,
        description="Override YAML iou_threshold",
    )
    person_class_id: int | None = Field(
        default=None,
        description="Override YAML person_class_id (COCO person = 0)",
    )

    # Video I/O
    video_source: Path | None = Field(
        default=None,
        validation_alias="VIDEO_SOURCE",
        description="Single .mp4 file or directory of videos",
    )
    video_glob: str = Field(
        default="*.mp4",
        description="Glob when video_source is a directory",
    )
    output_dir: Path | None = Field(
        default=None,
        description="Optional directory for annotated frames / summaries",
    )

    # Processing
    fps_limit: int | None = Field(
        default=None,
        ge=1,
        description="Max processing FPS; None = process every frame",
    )
    max_consecutive_drop_frames: int = Field(
        default=30,
        ge=1,
        description="Abort video after this many consecutive unreadable frames",
    )
    device: str = Field(
        default="cpu",
        description="Ultralytics device string, e.g. cpu, 0, cuda:0",
    )

    # Modes
    visualize: bool = Field(default=False, description="Show OpenCV window with boxes")
    save_annotated: bool = Field(
        default=False,
        description="Write annotated MP4 under output_dir",
    )

    # Tracking (ByteTrack + history store)
    track_timeout_frames: int | None = Field(
        default=None,
        ge=1,
        description="Frames before a LOST track becomes ENDED; defaults to track_buffer",
    )
    track_history_max_points: int = Field(default=500, ge=10)
    trajectory_length: int = Field(default=30, ge=2, description="Viz polyline length")
    group_entry_min_size: int = Field(default=2, ge=2)
    group_entry_frame_window: int = Field(default=1, ge=0)

    @field_validator("model_path", "video_source", "output_dir", "models_config_path", mode="before")
    @classmethod
    def expand_path(cls, v: Any) -> Any:
        if v is None or isinstance(v, Path):
            return v
        text = str(v).strip()
        if not text:
            return None
        return Path(text).expanduser()

    def load_detector_yaml(self) -> DetectorYamlConfig:
        """Load detector block from models YAML; raises if file missing."""
        path = self.models_config_path
        if not path.is_file():
            raise FileNotFoundError(f"Models config not found: {path.resolve()}")
        with path.open(encoding="utf-8") as fh:
            data: dict[str, Any] = yaml.safe_load(fh) or {}
        detector = data.get("detector", data)
        return DetectorYamlConfig.model_validate(detector)

    def resolved_model_path(self) -> str:
        """
        Resolve YOLO weights path.

        Priority: explicit model_path env > YAML model_name (download if needed).
        """
        if self.model_path is not None:
            if not self.model_path.exists():
                raise FileNotFoundError(f"Model weights not found: {self.model_path.resolve()}")
            return str(self.model_path.resolve())
        yaml_cfg = self.load_detector_yaml()
        return yaml_cfg.model_name

    def resolved_confidence(self) -> float:
        if self.confidence_threshold is not None:
            return self.confidence_threshold
        return self.load_detector_yaml().confidence_threshold

    def resolved_iou(self) -> float:
        if self.iou_threshold is not None:
            return self.iou_threshold
        return self.load_detector_yaml().iou_threshold

    def resolved_person_class_id(self) -> int:
        if self.person_class_id is not None:
            return self.person_class_id
        return self.load_detector_yaml().person_class_id

    def load_tracker_yaml(self) -> TrackerYamlConfig:
        """Load tracker block from models YAML."""
        path = self.models_config_path
        if not path.is_file():
            raise FileNotFoundError(f"Models config not found: {path.resolve()}")
        with path.open(encoding="utf-8") as fh:
            data: dict[str, Any] = yaml.safe_load(fh) or {}
        tracker = data.get("tracker", {})
        return TrackerYamlConfig.model_validate(tracker)

    def resolved_track_timeout(self) -> int:
        """App-level dropped-track timeout; defaults to ByteTrack buffer size."""
        if self.track_timeout_frames is not None:
            return self.track_timeout_frames
        return self.load_tracker_yaml().track_buffer
