"""YOLOv8n person detection — frame-by-frame video processing (no tracking)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import numpy as np
from ultralytics import YOLO
from ultralytics.engine.results import Results

from pipeline.config import DetectionConfig
from pipeline.utils import (
    AnnotatedVideoWriter,
    FpsThrottle,
    VideoReader,
    draw_detections,
    ensure_directory,
    iter_video_paths,
    show_frame,
)
from shared.logging import configure_logging, get_logger

logger = get_logger(__name__)

WINDOW_NAME = "store-intelligence-detection"


@dataclass(frozen=True)
class PersonDetection:
    """Single person detection for one frame."""

    bbox_xyxy: tuple[int, int, int, int]
    confidence: float
    class_id: int
    class_name: str


@dataclass(frozen=True)
class FrameDetections:
    """All person detections for a single video frame."""

    frame_index: int
    detections: tuple[PersonDetection, ...]
    inference_ms: float | None = None


@dataclass
class VideoDetectionSummary:
    """Aggregated stats after processing one video."""

    video_path: Path
    frames_processed: int
    frames_dropped: int
    total_detections: int
    avg_persons_per_frame: float
    avg_inference_ms: float | None


class PersonDetector:
    """
    YOLOv8 person detector — filters to a single class with confidence threshold.

    Tracking is intentionally not implemented; each frame is independent.
    """

    def __init__(
        self,
        *,
        model_path: str,
        confidence: float,
        iou: float,
        person_class_id: int,
        device: str,
    ) -> None:
        self._confidence = confidence
        self._iou = iou
        self._person_class_id = person_class_id
        self._device = device

        logger.info(
            "loading_yolo_model",
            model_path=model_path,
            confidence=confidence,
            iou=iou,
            person_class_id=person_class_id,
            device=device,
        )
        try:
            self._model = YOLO(model_path)
        except Exception as exc:
            logger.error("model_load_failed", model_path=model_path, error=str(exc))
            raise RuntimeError(f"Failed to load YOLO model: {model_path}") from exc

        # TODO: optional TensorRT / ONNX export for lower latency in production
        # TODO: batch inference across frames when GPU memory allows

    def detect_frame(self, frame: np.ndarray, frame_index: int) -> FrameDetections:
        """
        Run inference on one BGR frame and return person detections only.

        Args:
            frame: OpenCV BGR image
            frame_index: Monotonic index in the source video

        Returns:
            FrameDetections with scores and pixel bounding boxes
        """
        t0 = perf_counter()
        try:
            results: list[Results] = self._model.predict(
                source=frame,
                conf=self._confidence,
                iou=self._iou,
                classes=[self._person_class_id],
                device=self._device,
                verbose=False,
            )
        except Exception as exc:
            logger.error("inference_failed", frame_index=frame_index, error=str(exc))
            raise

        inference_ms = (perf_counter() - t0) * 1000.0
        detections = self._parse_results(results[0] if results else None)
        return FrameDetections(
            frame_index=frame_index,
            detections=detections,
            inference_ms=inference_ms,
        )

    def _parse_results(self, result: Results | None) -> tuple[PersonDetection, ...]:
        if result is None or result.boxes is None or len(result.boxes) == 0:
            return ()

        out: list[PersonDetection] = []
        boxes = result.boxes
        xyxy = boxes.xyxy.cpu().numpy()
        confs = boxes.conf.cpu().numpy()
        cls_ids = boxes.cls.cpu().numpy().astype(int)
        names = result.names or {}

        for i in range(len(xyxy)):
            class_id = int(cls_ids[i])
            if class_id != self._person_class_id:
                continue
            x1, y1, x2, y2 = (int(v) for v in xyxy[i])
            confidence = float(confs[i])
            class_name = str(names.get(class_id, "person"))
            out.append(
                PersonDetection(
                    bbox_xyxy=(x1, y1, x2, y2),
                    confidence=confidence,
                    class_id=class_id,
                    class_name=class_name,
                )
            )
        return tuple(out)


class DetectionRunner:
    """
    Orchestrates video ingestion, per-frame detection, visualization, and outputs.

    Modular entry point used by CLI and (later) the full analytics pipeline.
    """

    def __init__(self, config: DetectionConfig) -> None:
        self._config = config
        self._detector = PersonDetector(
            model_path=config.resolved_model_path(),
            confidence=config.resolved_confidence(),
            iou=config.resolved_iou(),
            person_class_id=config.resolved_person_class_id(),
            device=config.device,
        )
        self._throttle = FpsThrottle(config.fps_limit)

    def run_all(self, video_paths: list[Path]) -> list[VideoDetectionSummary]:
        """Process multiple videos sequentially; failures are logged and skipped."""
        summaries: list[VideoDetectionSummary] = []
        for path in video_paths:
            try:
                summary = self.run_video(path)
                summaries.append(summary)
            except FileNotFoundError:
                logger.error("video_not_found", path=str(path))
            except RuntimeError as exc:
                logger.error("video_processing_failed", path=str(path), error=str(exc))
            except Exception as exc:
                logger.exception("video_unexpected_error", path=str(path), error=str(exc))
        return summaries

    def run_video(self, video_path: Path) -> VideoDetectionSummary:
        """Process a single MP4 file frame-by-frame."""
        path = video_path.expanduser().resolve()
        logger.info("video_processing_start", path=str(path))

        output_dir = ensure_directory(self._config.output_dir)
        writer: AnnotatedVideoWriter | None = None
        frames_processed = 0
        total_detections = 0
        inference_times: list[float] = []

        reader = VideoReader(
            path=path,
            max_consecutive_drops=self._config.max_consecutive_drop_frames,
        )

        with reader:
            meta = reader.metadata
            if self._config.save_annotated and output_dir is not None:
                out_file = output_dir / f"{path.stem}_annotated.mp4"
                writer = AnnotatedVideoWriter(
                    output_path=out_file,
                    width=meta.width,
                    height=meta.height,
                    fps=meta.fps or 25.0,
                )
                writer.open()

            for read_result in reader.frames():
                if read_result.dropped or read_result.frame is None:
                    continue

                self._throttle.wait()
                frame_result = self._detector.detect_frame(
                    read_result.frame,
                    read_result.frame_index,
                )
                frames_processed += 1
                total_detections += len(frame_result.detections)
                if frame_result.inference_ms is not None:
                    inference_times.append(frame_result.inference_ms)

                if frames_processed % 100 == 0:
                    logger.info(
                        "detection_progress",
                        path=str(path),
                        frame_index=read_result.frame_index,
                        persons=len(frame_result.detections),
                        inference_ms=round(frame_result.inference_ms or 0, 1),
                    )

                if self._config.visualize or self._config.save_annotated:
                    annotated = draw_detections(read_result.frame, frame_result)
                    if writer is not None:
                        writer.write(annotated)
                    if self._config.visualize:
                        if not show_frame(WINDOW_NAME, annotated):
                            logger.info("visualization_stopped_by_user", path=str(path))
                            break

        if writer is not None:
            writer.close()
        if self._config.visualize:
            cv2_destroy_window_safe()

        avg_inference = (
            sum(inference_times) / len(inference_times) if inference_times else None
        )
        summary = VideoDetectionSummary(
            video_path=path,
            frames_processed=frames_processed,
            frames_dropped=reader.stats.frames_dropped,
            total_detections=total_detections,
            avg_persons_per_frame=(
                total_detections / frames_processed if frames_processed else 0.0
            ),
            avg_inference_ms=avg_inference,
        )
        logger.info("video_processing_complete", **summary.__dict__)
        return summary

    def iter_detections(self, video_path: Path) -> Iterator[FrameDetections]:
        """
        Lazy iterator for downstream pipeline stages (tracking, events).

        Skips dropped frames without yielding.
        """
        reader = VideoReader(
            path=video_path,
            max_consecutive_drops=self._config.max_consecutive_drop_frames,
        )
        with reader:
            for read_result in reader.frames():
                if read_result.dropped or read_result.frame is None:
                    continue
                self._throttle.wait()
                yield self._detector.detect_frame(read_result.frame, read_result.frame_index)


def cv2_destroy_window_safe() -> None:
    import cv2

    try:
        cv2.destroyWindow(WINDOW_NAME)
        cv2.waitKey(1)
    except Exception:
        pass


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="YOLOv8n person detection on MP4 video(s)",
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Path to .mp4 file or directory of videos",
    )
    parser.add_argument(
        "--model-path",
        type=Path,
        default=None,
        help="YOLO weights file (overrides configs/models.yaml)",
    )
    parser.add_argument(
        "--models-config",
        type=Path,
        default=None,
        help="Path to models.yaml",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=None,
        help="Detection confidence threshold",
    )
    parser.add_argument(
        "--iou",
        type=float,
        default=None,
        help="NMS IoU threshold",
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="Inference device (cpu, 0, cuda:0)",
    )
    parser.add_argument(
        "--fps-limit",
        type=int,
        default=None,
        help="Max processing FPS",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for annotated output videos",
    )
    parser.add_argument(
        "--glob",
        type=str,
        default="*.mp4",
        help="Glob for batch directory mode",
    )
    parser.add_argument(
        "--visualize",
        action="store_true",
        help="Show OpenCV window (press q to quit)",
    )
    parser.add_argument(
        "--save-annotated",
        action="store_true",
        help="Write annotated MP4 to output-dir",
    )
    parser.add_argument(
        "--log-json",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> DetectionConfig:
    """Build DetectionConfig from CLI, falling back to env via pydantic-settings."""
    overrides: dict[str, object] = {"video_source": args.source}
    if args.model_path is not None:
        overrides["model_path"] = args.model_path
    if args.models_config is not None:
        overrides["models_config_path"] = args.models_config
    if args.confidence is not None:
        overrides["confidence_threshold"] = args.confidence
    if args.iou is not None:
        overrides["iou_threshold"] = args.iou
    if args.device is not None:
        overrides["device"] = args.device
    if args.fps_limit is not None:
        overrides["fps_limit"] = args.fps_limit
    if args.output_dir is not None:
        overrides["output_dir"] = args.output_dir
    overrides["visualize"] = args.visualize
    overrides["save_annotated"] = args.save_annotated
    overrides["video_glob"] = args.glob
    overrides["log_json"] = args.log_json
    overrides["log_level"] = args.log_level
    return DetectionConfig(**overrides)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for detection-only pipeline."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    configure_logging(json_logs=args.log_json, log_level=args.log_level)

    try:
        config = config_from_args(args)
        paths = iter_video_paths(config.video_source or args.source, config.video_glob)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("invalid_source", error=str(exc))
        return 1

    runner = DetectionRunner(config)
    summaries = runner.run_all(paths)

    if not summaries:
        logger.error("no_videos_processed")
        return 1

    logger.info("batch_complete", videos=len(summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
