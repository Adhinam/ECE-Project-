"""Pipeline-specific environment settings."""

from pydantic_settings import SettingsConfigDict

from shared.settings import AppSettings


class PipelineSettings(AppSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    pipeline_video_source: str = "/data/sample/demo.mp4"
    pipeline_store_id: str = "store-001"
    pipeline_camera_id: str = "cam-entrance"
    pipeline_fps_limit: int = 10
    enable_reid: bool = False
    enable_gpu: bool = False
    store_layout_path: str = "configs/store_layout.yaml"
    models_config_path: str = "configs/models.yaml"
    events_output_dir: str = "data/events"
    ingest_api_url: str = "http://localhost:8000"
    ingest_batch_size: int = 50
    ingest_flush_seconds: float = 1.0
    degrade_reid: bool = False
    degrade_staff_classifier: bool = False
