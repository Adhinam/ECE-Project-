"""ByteTrack visitor tracking via supervision — stable IDs, history, visualization."""

from __future__ import annotations

import argparse
import inspect
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

import cv2
import numpy as np
import supervision as sv

from pipeline.config import DetectionConfig, TrackerYamlConfig
from pipeline.detect import FrameDetections, PersonDetector
from pipeline.track_state import (
    GroupEntryCandidate,
    TrackHistoryStore,
    TrackPoint,
    TrackStatus,
    VisitorMovementHistory,
    VisitorTrack,
    bbox_centroid,
)
from pipeline.utils import (
    AnnotatedVideoWriter,
    FpsThrottle,
    VideoReader,
    ensure_directory,
    iter_video_paths,
    show_frame,
)
from shared.logging import configure_logging, get_logger

logger = get_logger(__name__)

WINDOW_NAME = "store-intelligence-tracking"


@dataclass(frozen=True)
class TrackedVisitor:
    """One tracked person in a single frame."""

    track_id: int
    bbox_xyxy: tuple[int, int, int, int]
    confidence: float
    centroid: tuple[float, float]
    class_id: int = 0


@dataclass(frozen=True)
class FrameTracks:
    """Tracking output for one video frame."""

    frame_index: int
    tracks: tuple[TrackedVisitor, ...]
    group_entries: tuple[GroupEntryCandidate, ...] = ()
    inference_ms: float | None = None
    tracking_ms: float | None = None


@dataclass
class VideoTrackingSummary:
    """Aggregated stats after processing one video with tracking."""

    video_path: Path
    frames_processed: int
    frames_dropped: int
    unique_track_ids: int
    active_at_end: int
    ended_tracks: int
    group_entries_detected: int
    avg_tracks_per_frame: float


def _create_byte_tracker(cfg: TrackerYamlConfig, *, frame_rate: float) -> sv.ByteTrack:
    """
    Construct supervision ByteTrack with version-tolerant parameter names.

    Newer supervision uses track_activation_threshold / lost_track_buffer;
    older builds use track_thresh / track_buffer.
    """
    frame_rate_int = max(1, int(round(frame_rate)) or 30)
    sig = inspect.signature(sv.ByteTrack.__init__)
    params = set(sig.parameters.keys())

    preferred_kwargs: dict[str, object] = {
        "track_activation_threshold": cfg.track_thresh,
        "lost_track_buffer": cfg.track_buffer,
        "minimum_matching_threshold": cfg.match_thresh,
        "frame_rate": frame_rate_int,
    }
    legacy_kwargs: dict[str, object] = {
        "track_thresh": cfg.track_thresh,
        "track_buffer": cfg.track_buffer,
        "match_thresh": cfg.match_thresh,
        "frame_rate": frame_rate_int,
    }

    # Some supervision versions expose proxy signatures that do not match runtime kwargs.
    attempts: list[dict[str, object]] = []
    if "track_activation_threshold" in params:
        attempts = [preferred_kwargs, legacy_kwargs]
    elif "track_thresh" in params:
        attempts = [legacy_kwargs, preferred_kwargs]
    else:
        attempts = [preferred_kwargs, legacy_kwargs]

    last_error: Exception | None = None
    for kwargs in attempts:
        try:
            logger.info("byte_track_initialized", **kwargs)
            return sv.ByteTrack(**kwargs)
        except TypeError as exc:
            last_error = exc
            logger.warning("byte_track_init_retry", error=str(exc), kwargs=kwargs)
            continue

    raise RuntimeError(f"Failed to initialize ByteTrack: {last_error}")


def detections_to_supervision(frame: FrameDetections) -> sv.Detections:
    """Convert pipeline detections to supervision Detections for ByteTrack."""
    if not frame.detections:
        return sv.Detections.empty()

    xyxy = np.array([d.bbox_xyxy for d in frame.detections], dtype=np.float32)
    confidence = np.array([d.confidence for d in frame.detections], dtype=np.float32)
    class_id = np.array([d.class_id for d in frame.detections], dtype=int)
    return sv.Detections(xyxy=xyxy, confidence=confidence, class_id=class_id)


def supervision_to_tracked_visitors(
    tracked: sv.Detections,
    *,
    frame_index: int,
) -> list[TrackedVisitor]:
    """Map supervision output to typed TrackedVisitor list (skip untracked)."""
    if tracked.is_empty() or tracked.tracker_id is None:
        return []

    visitors: list[TrackedVisitor] = []
    for i in range(len(tracked)):
        tid = tracked.tracker_id[i]
        if tid is None or int(tid) < 0:
            continue
        bbox = tuple(int(v) for v in tracked.xyxy[i])
        conf = float(tracked.confidence[i]) if tracked.confidence is not None else 0.0
        cid = int(tracked.class_id[i]) if tracked.class_id is not None else 0
        c = bbox_centroid(bbox)
        visitors.append(
            TrackedVisitor(
                track_id=int(tid),
                bbox_xyxy=bbox,
                confidence=conf,
                centroid=c.as_tuple(),
                class_id=cid,
            )
        )
    return visitors


class ByteTrackVisitorTracker:
    """
    Wraps supervision ByteTrack and TrackHistoryStore for stable visitor IDs.

    Re-identification is not used; track_id comes from ByteTrack only.
    """

    def __init__(
        self,
        *,
        tracker_config: TrackerYamlConfig,
        frame_rate: float,
        track_timeout_frames: int,
        max_history_points: int = 500,
        group_entry_min_size: int = 2,
    ) -> None:
        self._tracker_config = tracker_config
        self._track_timeout_frames = track_timeout_frames
        self._max_history_points = max_history_points
        self._group_entry_min_size = group_entry_min_size
        self._frame_rate = frame_rate
        self._byte_track = _create_byte_tracker(tracker_config, frame_rate=frame_rate)
        self._history = self._new_history_store()

    @property
    def history_store(self) -> TrackHistoryStore:
        return self._history

    def _new_history_store(self) -> TrackHistoryStore:
        return TrackHistoryStore(
            track_timeout_frames=self._track_timeout_frames,
            max_history_points=self._max_history_points,
            group_entry_min_size=self._group_entry_min_size,
        )

    def reset(self) -> None:
        """Reset per-video state (new ByteTrack instance + empty history)."""
        # TODO: reuse Kalman state if supervision exposes reset API
        self._byte_track = _create_byte_tracker(self._tracker_config, frame_rate=self._frame_rate)
        self._history = self._new_history_store()

    def update(self, frame: FrameDetections) -> FrameTracks:
        """
        Run ByteTrack on detections and update movement history.

        Occlusion recovery is handled by ByteTrack's lost-track buffer plus our
        LOST → ACTIVE transition when the same track_id reappears.
        """
        t0 = perf_counter()
        sv_det = detections_to_supervision(frame)
        tracked = self._byte_track.update_with_detections(sv_det)
        tracking_ms = (perf_counter() - t0) * 1000.0

        visitors = supervision_to_tracked_visitors(tracked, frame_index=frame.frame_index)
        points: list[TrackPoint] = []
        tids: list[int] = []
        for v in visitors:
            tids.append(v.track_id)
            points.append(
                TrackPoint(
                    frame_index=frame.frame_index,
                    centroid=bbox_centroid(v.bbox_xyxy),
                    bbox_xyxy=v.bbox_xyxy,
                    confidence=v.confidence,
                )
            )

        self._history.update_frame(frame.frame_index, points, track_ids=tids)
        groups = tuple(self._history.detect_group_entries(frame.frame_index))

        if groups:
            logger.debug(
                "group_entry_detected",
                frame_index=frame.frame_index,
                track_ids=groups[0].track_ids,
                size=groups[0].group_size,
            )

        return FrameTracks(
            frame_index=frame.frame_index,
            tracks=tuple(visitors),
            group_entries=groups,
            inference_ms=frame.inference_ms,
            tracking_ms=tracking_ms,
        )

    def get_movement_histories(self, *, include_ended: bool = True) -> list[VisitorMovementHistory]:
        return list(self._history.iter_histories(include_ended=include_ended))

    def get_track(self, track_id: int) -> VisitorTrack | None:
        return self._history.get(track_id)


def _color_for_track(track_id: int) -> tuple[int, int, int]:
    """Deterministic BGR color per track for visualization."""
    rng = (track_id * 37 + 17) % 255
    return (int(50 + rng), int(180 - rng / 2), int(100 + (track_id * 13) % 155))


def draw_tracked_frame(
    frame: np.ndarray,
    frame_tracks: FrameTracks,
    history_store: TrackHistoryStore,
    *,
    trajectory_length: int = 30,
    draw_boxes: bool = True,
    draw_ids: bool = True,
    draw_trajectories: bool = True,
) -> np.ndarray:
    """
    Visualize track IDs, bounding boxes, and centroid trajectories.

    Args:
        frame: BGR image
        frame_tracks: Current-frame tracks
        history_store: Full track history for polylines
        trajectory_length: Max past points per trajectory
    """
    annotated = frame.copy()

    if draw_trajectories:
        for visitor in frame_tracks.tracks:
            track = history_store.get(visitor.track_id)
            if track is None:
                continue
            pts = track.recent_centroids(trajectory_length)
            if len(pts) < 2:
                continue
            color = _color_for_track(visitor.track_id)
            poly = np.array([[int(p.x), int(p.y)] for p in pts], dtype=np.int32)
            cv2.polylines(annotated, [poly], isClosed=False, color=color, thickness=2)

    if draw_boxes or draw_ids:
        for visitor in frame_tracks.tracks:
            x1, y1, x2, y2 = visitor.bbox_xyxy
            color = _color_for_track(visitor.track_id)
            if draw_boxes:
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            if draw_ids:
                label = f"ID {visitor.track_id} {visitor.confidence:.2f}"
                cv2.putText(
                    annotated,
                    label,
                    (x1, max(y1 - 8, 0)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    color,
                    2,
                    cv2.LINE_AA,
                )
            # Centroid dot
            cx, cy = int(visitor.centroid[0]), int(visitor.centroid[1])
            cv2.circle(annotated, (cx, cy), 4, color, -1)

    hud = (
        f"frame={frame_tracks.frame_index} tracks={len(frame_tracks.tracks)} "
        f"groups={len(frame_tracks.group_entries)}"
    )
    cv2.putText(
        annotated,
        hud,
        (10, 24),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.7,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )
    return annotated


class TrackingRunner:
    """Detection + tracking over video sources."""

    def __init__(self, config: DetectionConfig) -> None:
        self._config = config
        self._detector = PersonDetector(
            model_path=config.resolved_model_path(),
            confidence=config.resolved_confidence(),
            iou=config.resolved_iou(),
            person_class_id=config.resolved_person_class_id(),
            device=config.device,
        )
        self._tracker_cfg = config.load_tracker_yaml()
        self._throttle = FpsThrottle(config.fps_limit)

    @property
    def detector(self) -> PersonDetector:
        return self._detector

    @property
    def tracker_config(self) -> TrackerYamlConfig:
        return self._tracker_cfg

    def run_all(self, video_paths: list[Path]) -> list[VideoTrackingSummary]:
        summaries: list[VideoTrackingSummary] = []
        for path in video_paths:
            try:
                summaries.append(self.run_video(path))
            except FileNotFoundError:
                logger.error("video_not_found", path=str(path))
            except RuntimeError as exc:
                logger.error("video_tracking_failed", path=str(path), error=str(exc))
            except Exception as exc:
                logger.exception("video_tracking_unexpected", path=str(path), error=str(exc))
        return summaries

    def run_video(self, video_path: Path) -> VideoTrackingSummary:
        path = video_path.expanduser().resolve()
        logger.info("tracking_start", path=str(path))

        output_dir = ensure_directory(self._config.output_dir)
        writer: AnnotatedVideoWriter | None = None
        frames_processed = 0
        total_track_instances = 0
        group_entries = 0

        reader = VideoReader(
            path=path,
            max_consecutive_drops=self._config.max_consecutive_drop_frames,
        )

        with reader:
            meta = reader.metadata
            tracker = ByteTrackVisitorTracker(
                tracker_config=self._tracker_cfg,
                frame_rate=meta.fps or 30.0,
                track_timeout_frames=self._config.resolved_track_timeout(),
                max_history_points=self._config.track_history_max_points,
                group_entry_min_size=self._config.group_entry_min_size,
            )

            if self._config.save_annotated and output_dir is not None:
                out_file = output_dir / f"{path.stem}_tracked.mp4"
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
                detections = self._detector.detect_frame(
                    read_result.frame,
                    read_result.frame_index,
                )
                frame_tracks = tracker.update(detections)
                frames_processed += 1
                total_track_instances += len(frame_tracks.tracks)
                group_entries += len(frame_tracks.group_entries)

                if frames_processed % 100 == 0:
                    logger.info(
                        "tracking_progress",
                        path=str(path),
                        frame_index=read_result.frame_index,
                        tracks=len(frame_tracks.tracks),
                        tracking_ms=round(frame_tracks.tracking_ms or 0, 1),
                    )

                if self._config.visualize or self._config.save_annotated:
                    annotated = draw_tracked_frame(
                        read_result.frame,
                        frame_tracks,
                        tracker.history_store,
                        trajectory_length=self._config.trajectory_length,
                    )
                    if writer is not None:
                        writer.write(annotated)
                    if self._config.visualize:
                        if not show_frame(WINDOW_NAME, annotated):
                            logger.info("tracking_visualization_stopped", path=str(path))
                            break

        if writer is not None:
            writer.close()
        if self._config.visualize:
            _destroy_window_safe()

        store = tracker.history_store
        unique_ids = len(store.tracks)
        active = sum(1 for t in store.tracks.values() if t.status == TrackStatus.ACTIVE)
        ended = sum(1 for t in store.tracks.values() if t.status == TrackStatus.ENDED)

        summary = VideoTrackingSummary(
            video_path=path,
            frames_processed=frames_processed,
            frames_dropped=reader.stats.frames_dropped,
            unique_track_ids=unique_ids,
            active_at_end=active,
            ended_tracks=ended,
            group_entries_detected=group_entries,
            avg_tracks_per_frame=(
                total_track_instances / frames_processed if frames_processed else 0.0
            ),
        )
        logger.info("tracking_complete", **summary.__dict__)
        return summary

    def iter_tracks(self, video_path: Path) -> Iterator[FrameTracks]:
        """Lazy iterator for event pipeline (entry/exit, zones)."""
        reader = VideoReader(
            path=video_path,
            max_consecutive_drops=self._config.max_consecutive_drop_frames,
        )
        with reader:
            meta = reader.metadata
            tracker = ByteTrackVisitorTracker(
                tracker_config=self._tracker_cfg,
                frame_rate=meta.fps or 30.0,
                track_timeout_frames=self._config.resolved_track_timeout(),
                max_history_points=self._config.track_history_max_points,
                group_entry_min_size=self._config.group_entry_min_size,
            )
            for read_result in reader.frames():
                if read_result.dropped or read_result.frame is None:
                    continue
                self._throttle.wait()
                detections = self._detector.detect_frame(
                    read_result.frame,
                    read_result.frame_index,
                )
                yield tracker.update(detections)


def _destroy_window_safe() -> None:
    try:
        cv2.destroyWindow(WINDOW_NAME)
        cv2.waitKey(1)
    except Exception:
        pass


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="YOLOv8 + ByteTrack visitor tracking")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, default=None)
    parser.add_argument("--models-config", type=Path, default=None)
    parser.add_argument("--confidence", type=float, default=None)
    parser.add_argument("--iou", type=float, default=None)
    parser.add_argument("--device", type=str, default=None)
    parser.add_argument("--fps-limit", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--glob", type=str, default="*.mp4")
    parser.add_argument("--visualize", action="store_true")
    parser.add_argument("--save-annotated", action="store_true")
    parser.add_argument("--track-timeout", type=int, default=None, help="Frames before ENDED")
    parser.add_argument("--trajectory-length", type=int, default=None)
    parser.add_argument("--log-json", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--log-level", type=str, default="INFO")
    return parser


def config_from_args(args: argparse.Namespace) -> DetectionConfig:
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
    if args.track_timeout is not None:
        overrides["track_timeout_frames"] = args.track_timeout
    if args.trajectory_length is not None:
        overrides["trajectory_length"] = args.trajectory_length
    overrides["visualize"] = args.visualize
    overrides["save_annotated"] = args.save_annotated
    overrides["video_glob"] = args.glob
    overrides["log_json"] = args.log_json
    overrides["log_level"] = args.log_level
    return DetectionConfig(**overrides)


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    configure_logging(json_logs=args.log_json, log_level=args.log_level)

    try:
        config = config_from_args(args)
        paths = iter_video_paths(config.video_source or args.source, config.video_glob)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("invalid_source", error=str(exc))
        return 1

    runner = TrackingRunner(config)
    summaries = runner.run_all(paths)
    if not summaries:
        logger.error("no_videos_processed")
        return 1
    logger.info("tracking_batch_complete", videos=len(summaries))
    return 0


if __name__ == "__main__":
    sys.exit(main())
