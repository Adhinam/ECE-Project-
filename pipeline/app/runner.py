"""Frame loop orchestration — wires detector, tracker, geometry, emitter."""

from __future__ import annotations

from pipeline.app.calibration import ConfidenceCalibrator
from pipeline.app.detector import YoloDetector
from pipeline.app.emitter import JsonlEventEmitter
from pipeline.app.entry_exit import EntryExitDetector
from pipeline.app.tracker import ByteTrackTracker
from pipeline.settings import PipelineSettings
from schemas.config import StoreLayoutConfig
from shared.logging import get_logger

logger = get_logger(__name__)


class PipelineRunner:
    def __init__(self, settings: PipelineSettings) -> None:
        self._settings = settings
        self._layout: StoreLayoutConfig | None = None
        self._frame_index = 0

    def _load_layout(self) -> StoreLayoutConfig:
        import yaml

        with open(self._settings.store_layout_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return StoreLayoutConfig.model_validate(data)

    async def run(self) -> None:
        """Delegate to the production frame loop in pipeline.orchestrator."""
        from pipeline.orchestrator import FullPipelineRunner

        logger.info("pipeline_starting_full_runner", store_id=self._settings.pipeline_store_id)
        runner = FullPipelineRunner(self._settings)
        summary = runner.run(self._settings.pipeline_video_source)
        logger.info(
            "pipeline_finished",
            frames=summary.frames_processed,
            events=summary.events_emitted,
            jsonl=str(summary.jsonl_path) if summary.jsonl_path else None,
        )
