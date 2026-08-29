"""Entry/exit detection — direction-aware line crossing from track centroids."""

from __future__ import annotations

import argparse
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

import cv2
import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from pipeline.config import DetectionConfig
from pipeline.tracker import FrameTracks, TrackingRunner, draw_tracked_frame
from pipeline.utils import (
    AnnotatedVideoWriter,
    ensure_directory,
    iter_video_paths,
    show_frame,
)
from schemas.config import EntryExitLine, StoreLayoutConfig
from schemas.events import EventEnvelope, EventType, generate_event_id
from shared.logging import configure_logging, get_logger

logger = get_logger(__name__)

WINDOW_NAME = "store-intelligence-entry-exit"

# Minimum cross-product magnitude to treat a point as off the line (pixels scaled).
_SIDE_EPSILON = 1e-3


class CrossingDirection(StrEnum):
    """Direction of a centroid crossing relative to the configured inward normal."""

    ENTRY = "entry"
    EXIT = "exit"


@dataclass(frozen=True)
class LineSegment:
    """Directed line segment p1 -> p2."""

    p1: tuple[float, float]
    p2: tuple[float, float]
    line_id: str
    direction_in: str

    @property
    def as_tuple(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return (self.p1, self.p2)


class EntryExitSettings(BaseSettings):
    """Runtime tuning for line-crossing detection."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    cooldown_frames: int = Field(
        default=30,
        ge=0,
        description="Min frames between same event type per track and line",
    )
    debounce_frames: int = Field(
        default=2,
        ge=0,
        description="Frames centroid must stay on new side before arming another cross",
    )
    min_crossing_displacement_px: float = Field(
        default=4.0,
        ge=0.0,
        description="Min movement along inward normal to count a crossing",
    )
    group_crossing_frame_window: int = Field(
        default=0,
        ge=0,
        description="Frames within which simultaneous crossings share a group id",
    )
    active_line_id: str | None = Field(
        default=None,
        description="If set, only this line id is evaluated",
    )
    entry_confidence_floor: float = Field(default=0.5, ge=0.0, le=1.0)
    calibration_method: str = "threshold_v1"


@dataclass
class VideoClock:
    """Map frame indices to wall-clock timestamps for event envelopes."""

    fps: float
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def timestamp_for_frame(self, frame_index: int) -> datetime:
        if self.fps <= 0:
            return self.started_at
        offset = timedelta(seconds=frame_index / self.fps)
        return self.started_at + offset


@dataclass
class TrackLineState:
    """Per (track, line) crossing state for debounce and cooldown."""

    last_centroid: tuple[float, float] | None = None
    last_side: int = 0
    stable_side: int = 0
    stable_side_frames: int = 0
    last_entry_frame: int = -10_000
    last_exit_frame: int = -10_000
    armed: bool = True


def _cross_sign(line: LineSegment, point: tuple[float, float]) -> float:
    """
    Signed cross product of (p1->p2) with (p1->point).

    Positive / negative values indicate which side of the line the point lies on.
    """
    ax, ay = line.p1
    bx, by = line.p2
    px, py = point
    return (bx - ax) * (py - ay) - (by - ay) * (px - ax)


def _inward_normal(line: LineSegment) -> tuple[float, float]:
    """
    Unit normal pointing into the store for ENTRY direction.

    `direction_in` selects which perpendicular indicates inward:
    - bottom: inward is upward (-y) when line is more vertical
    - top: inward is downward (+y)
    - left / right: based on line orientation
    """
    ax, ay = line.p1
    bx, by = line.p2
    dx, dy = bx - ax, by - ay
    length = (dx * dx + dy * dy) ** 0.5
    if length < 1e-6:
        return (0.0, -1.0)

    # Left normal of p1->p2
    nx, ny = -dy / length, dx / length
    direction = line.direction_in.lower()

    if direction == "bottom":
        return (nx, ny) if ny < 0 else (-nx, -ny)
    if direction == "top":
        return (nx, ny) if ny > 0 else (-nx, -ny)
    if direction == "left":
        return (nx, ny) if nx < 0 else (-nx, -ny)
    if direction == "right":
        return (nx, ny) if nx > 0 else (-nx, -ny)
    # Default: left normal
    return (nx, ny)


def _segments_intersect(
    a1: tuple[float, float],
    a2: tuple[float, float],
    b1: tuple[float, float],
    b2: tuple[float, float],
) -> bool:
    """Return True if segment a1-a2 intersects segment b1-b2 (2D)."""

    def orient(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1 = orient(a1, a2, b1)
    o2 = orient(a1, a2, b2)
    o3 = orient(b1, b2, a1)
    o4 = orient(b1, b2, a2)

    if o1 == 0 and o2 == 0 and o3 == 0 and o4 == 0:
        return False
    return (o1 * o2 < 0) and (o3 * o4 < 0)


def _side_from_sign(value: float) -> int:
    if value > _SIDE_EPSILON:
        return 1
    if value < -_SIDE_EPSILON:
        return -1
    return 0


def classify_crossing(
    prev: tuple[float, float],
    curr: tuple[float, float],
    line: LineSegment,
) -> CrossingDirection | None:
    """
    Classify ENTRY vs EXIT from centroid motion across a line.

    Uses segment intersection plus inward-normal dot product for direction.
    """
    prev_sign = _side_from_sign(_cross_sign(line, prev))
    curr_sign = _side_from_sign(_cross_sign(line, curr))
    crossed_segment = _segments_intersect(prev, curr, line.p1, line.p2)

    if not crossed_segment:
        if prev_sign == 0 or curr_sign == 0 or prev_sign == curr_sign:
            return None
    elif prev_sign != 0 and curr_sign != 0 and prev_sign == curr_sign:
        return None

    nx, ny = _inward_normal(line)
    displacement = (curr[0] - prev[0], curr[1] - prev[1])
    normal_component = displacement[0] * nx + displacement[1] * ny

    if abs(normal_component) < 1e-6:
        return None

    if normal_component > 0:
        return CrossingDirection.ENTRY
    return CrossingDirection.EXIT


def line_from_config(cfg: EntryExitLine) -> LineSegment:
    return LineSegment(
        p1=(float(cfg.p1[0]), float(cfg.p1[1])),
        p2=(float(cfg.p2[0]), float(cfg.p2[1])),
        line_id=cfg.id,
        direction_in=cfg.direction_in,
    )


@dataclass
class CrossingEvent:
    """Internal crossing record before envelope build."""

    direction: CrossingDirection
    track_id: int
    line_id: str
    frame_index: int
    confidence: float
    bbox: tuple[int, int, int, int]
    centroid: tuple[float, float]
    group_crossing_id: str | None = None


class EntryExitDetector:
    """
    Direction-aware entry/exit detector using track centroids.

    - Centroid-based crossing with segment intersection
    - Cooldown prevents duplicate ENTRY/EXIT for same track+line
    - Debounce requires stable side before re-arming
    - Group crossings share a group_crossing_id in payload
    """

    def __init__(
        self,
        layout: StoreLayoutConfig,
        settings: EntryExitSettings,
        *,
        camera_id: str,
        clock: VideoClock,
    ) -> None:
        self._layout = layout
        self._settings = settings
        self._camera_id = camera_id
        self._clock = clock
        self._lines: list[LineSegment] = [
            line_from_config(line)
            for line in layout.entry_exit_lines
            if settings.active_line_id is None or line.id == settings.active_line_id
        ]
        self._track_state: dict[tuple[int, str], TrackLineState] = {}
        self._recent_events: list[CrossingEvent] = []

        if not self._lines:
            logger.warning(
                "entry_exit_no_lines",
                store_id=layout.store_id,
                active_line_id=settings.active_line_id,
            )

    @property
    def recent_events(self) -> list[CrossingEvent]:
        return list(self._recent_events)

    def _state(self, track_id: int, line_id: str) -> TrackLineState:
        key = (track_id, line_id)
        if key not in self._track_state:
            self._track_state[key] = TrackLineState()
        return self._track_state[key]

    def _update_debounce(self, state: TrackLineState, side: int, frame_index: int) -> None:
        """Require stable side for debounce_frames before allowing another fire."""
        if side == 0:
            return
        if side == state.stable_side:
            state.stable_side_frames += 1
        else:
            state.stable_side = side
            state.stable_side_frames = 1
        if state.stable_side_frames >= self._settings.debounce_frames:
            state.armed = True

    def _cooldown_ok(
        self,
        state: TrackLineState,
        direction: CrossingDirection,
        frame_index: int,
    ) -> bool:
        if direction == CrossingDirection.ENTRY:
            return (frame_index - state.last_entry_frame) >= self._settings.cooldown_frames
        return (frame_index - state.last_exit_frame) >= self._settings.cooldown_frames

    def _record_fire(self, state: TrackLineState, direction: CrossingDirection, frame_index: int) -> None:
        if direction == CrossingDirection.ENTRY:
            state.last_entry_frame = frame_index
        else:
            state.last_exit_frame = frame_index
        state.armed = False
        state.stable_side_frames = 0

    def process_frame(self, frame_tracks: FrameTracks) -> list[EventEnvelope]:
        """
        Process one frame of tracks and emit ENTRY/EXIT events.

        Returns new EventEnvelope instances (may be empty).
        """
        frame_index = frame_tracks.frame_index
        raw_crossings: list[CrossingEvent] = []

        for visitor in frame_tracks.tracks:
            curr = visitor.centroid
            displacement_ok = True

            for line in self._lines:
                state = self._state(visitor.track_id, line.line_id)
                curr_side = _side_from_sign(_cross_sign(line, curr))

                if state.last_centroid is not None:
                    prev = state.last_centroid
                    nx, ny = _inward_normal(line)
                    normal_move = abs(
                        (curr[0] - prev[0]) * nx + (curr[1] - prev[1]) * ny
                    )
                    if normal_move < self._settings.min_crossing_displacement_px:
                        displacement_ok = False

                    direction = classify_crossing(prev, curr, line)
                    if (
                        direction is not None
                        and state.armed
                        and displacement_ok
                        and self._cooldown_ok(state, direction, frame_index)
                    ):
                        confidence = max(
                            visitor.confidence,
                            self._settings.entry_confidence_floor,
                        )
                        raw_crossings.append(
                            CrossingEvent(
                                direction=direction,
                                track_id=visitor.track_id,
                                line_id=line.line_id,
                                frame_index=frame_index,
                                confidence=min(1.0, confidence),
                                bbox=visitor.bbox_xyxy,
                                centroid=curr,
                            )
                        )
                        self._record_fire(state, direction, frame_index)
                        logger.info(
                            "line_crossing",
                            event_type=direction.value,
                            track_id=visitor.track_id,
                            line_id=line.line_id,
                            frame_index=frame_index,
                            confidence=round(confidence, 3),
                        )

                state.last_centroid = curr
                self._update_debounce(state, curr_side, frame_index)

        envelopes = self._apply_group_crossings(raw_crossings, frame_index)
        self._recent_events.extend(raw_crossings)
        if len(self._recent_events) > 50:
            self._recent_events = self._recent_events[-50:]
        return envelopes

    def _apply_group_crossings(
        self,
        crossings: list[CrossingEvent],
        frame_index: int,
    ) -> list[EventEnvelope]:
        """Assign group_crossing_id when multiple tracks cross the same line together."""
        if not crossings:
            return []

        by_line: dict[str, list[CrossingEvent]] = defaultdict(list)
        for c in crossings:
            by_line[c.line_id].append(c)

        envelopes: list[EventEnvelope] = []
        for line_id, group in by_line.items():
            group_id: str | None = None
            same_direction = len({c.direction for c in group}) == 1
            if len(group) >= 2 and same_direction:
                group_id = str(uuid.uuid4())
                logger.info(
                    "group_crossing",
                    line_id=line_id,
                    frame_index=frame_index,
                    direction=group[0].direction.value,
                    track_ids=[c.track_id for c in group],
                    group_crossing_id=group_id,
                )
            group_size = len(group) if group_id else 1
            for crossing in group:
                if group_id:
                    crossing.group_crossing_id = group_id
                envelopes.append(self._to_envelope(crossing, group_size=group_size))
        return envelopes

    def _to_envelope(self, crossing: CrossingEvent, *, group_size: int = 1) -> EventEnvelope:
        event_type = (
            EventType.ENTRY
            if crossing.direction == CrossingDirection.ENTRY
            else EventType.EXIT
        )
        payload: dict[str, Any] = {
            "line_id": crossing.line_id,
            "centroid": [crossing.centroid[0], crossing.centroid[1]],
        }
        if crossing.group_crossing_id:
            payload["group_crossing_id"] = crossing.group_crossing_id
            payload["group_size"] = group_size

        return EventEnvelope(
            event_id=generate_event_id(
                store_id=self._layout.store_id,
                camera_id=self._camera_id,
                event_type=event_type.value,
                track_id=crossing.track_id,
                frame_index=crossing.frame_index,
                subtype=f"{crossing.line_id}:{crossing.direction.value}",
            ),
            event_type=event_type,
            store_id=self._layout.store_id,
            camera_id=self._camera_id,
            occurred_at=self._clock.timestamp_for_frame(crossing.frame_index),
            track_id=crossing.track_id,
            global_person_id=f"track-{crossing.track_id}",
            is_staff=False,
            confidence=crossing.confidence,
            calibration_method=self._settings.calibration_method,
            bbox=crossing.bbox,
            frame_index=crossing.frame_index,
            payload=payload,
        )


def load_store_layout(path: Path) -> StoreLayoutConfig:
    if not path.is_file():
        raise FileNotFoundError(f"Store layout not found: {path.resolve()}")
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return StoreLayoutConfig.model_validate(data)


def load_entry_exit_settings(layout_path: Path) -> EntryExitSettings:
    """Merge optional `entry_exit` block from store_layout.yaml with env defaults."""
    settings = EntryExitSettings()
    if not layout_path.is_file():
        return settings
    with layout_path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    block = data.get("entry_exit")
    if block:
        settings = EntryExitSettings.model_validate(block)
    return settings


def draw_entry_exit_overlay(
    frame: np.ndarray,
    lines: list[LineSegment],
    *,
    recent_events: list[CrossingEvent] | None = None,
    debug: bool = True,
) -> np.ndarray:
    """
    Debug overlay: entry/exit lines, inward arrows, recent crossing labels.

    Args:
        frame: BGR image (often already annotated with tracks)
        lines: Configured lines
        recent_events: Last crossings to flash on HUD
        debug: Draw normals and line ids when True
    """
    overlay = frame.copy()
    for line in lines:
        p1 = (int(line.p1[0]), int(line.p1[1]))
        p2 = (int(line.p2[0]), int(line.p2[1]))
        cv2.line(overlay, p1, p2, (0, 255, 255), 2)
        if debug:
            mid = ((p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2)
            nx, ny = _inward_normal(line)
            tip = (int(mid[0] + nx * 40), int(mid[1] + ny * 40))
            cv2.arrowedLine(overlay, mid, tip, (255, 200, 0), 2, tipLength=0.3)
            cv2.putText(
                overlay,
                f"{line.line_id} in:{line.direction_in}",
                (p1[0], p1[1] - 6),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 255, 255),
                1,
                cv2.LINE_AA,
            )

    if recent_events:
        y = 48
        for ev in recent_events[-5:]:
            color = (0, 220, 0) if ev.direction == CrossingDirection.ENTRY else (0, 0, 220)
            text = f"{ev.direction.value.upper()} id={ev.track_id} line={ev.line_id} f={ev.frame_index}"
            cv2.putText(
                overlay,
                text,
                (10, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                cv2.LINE_AA,
            )
            y += 22
    return overlay


@dataclass
class EntryExitRunSummary:
    video_path: Path
    frames_processed: int
    entry_count: int
    exit_count: int
    group_crossings: int


class EntryExitRunner:
    """Detection + tracking + entry/exit over video sources."""

    def __init__(
        self,
        detection_config: DetectionConfig,
        *,
        store_layout_path: Path,
        camera_id: str = "cam-entrance",
        entry_exit_settings: EntryExitSettings | None = None,
    ) -> None:
        self._detection_config = detection_config
        self._layout = load_store_layout(store_layout_path)
        self._entry_exit_settings = entry_exit_settings or load_entry_exit_settings(
            store_layout_path
        )
        self._camera_id = camera_id
        self._tracking = TrackingRunner(detection_config)

    def run_video(self, video_path: Path) -> tuple[EntryExitRunSummary, list[EventEnvelope]]:
        from pipeline.tracker import ByteTrackVisitorTracker
        from pipeline.utils import FpsThrottle, VideoReader

        path = video_path.expanduser().resolve()
        logger.info("entry_exit_start", path=str(path), store_id=self._layout.store_id)

        all_events: list[EventEnvelope] = []
        entry_count = 0
        exit_count = 0
        group_crossing_ids: set[str] = set()
        frames_processed = 0

        output_dir = ensure_directory(self._detection_config.output_dir)
        writer: AnnotatedVideoWriter | None = None

        reader = VideoReader(
            path=path,
            max_consecutive_drops=self._detection_config.max_consecutive_drop_frames,
        )

        with reader:
            meta = reader.metadata
            clock = VideoClock(fps=meta.fps or 30.0)
            detector = EntryExitDetector(
                self._layout,
                self._entry_exit_settings,
                camera_id=self._camera_id,
                clock=clock,
            )
            lines = list(detector._lines)  # noqa: SLF001 — visualization

            byte_tracker = ByteTrackVisitorTracker(
                tracker_config=self._tracking.tracker_config,
                frame_rate=meta.fps or 30.0,
                track_timeout_frames=self._detection_config.resolved_track_timeout(),
                max_history_points=self._detection_config.track_history_max_points,
                group_entry_min_size=self._detection_config.group_entry_min_size,
            )
            person_detector = self._tracking.detector
            throttle = FpsThrottle(self._detection_config.fps_limit)

            if self._detection_config.save_annotated and output_dir is not None:
                out_path = output_dir / f"{path.stem}_entry_exit.mp4"
                writer = AnnotatedVideoWriter(
                    output_path=out_path,
                    width=meta.width,
                    height=meta.height,
                    fps=meta.fps or 25.0,
                )
                writer.open()

            for read_result in reader.frames():
                if read_result.dropped or read_result.frame is None:
                    continue
                throttle.wait()
                detections = person_detector.detect_frame(
                    read_result.frame,
                    read_result.frame_index,
                )
                frame_tracks = byte_tracker.update(detections)
                frames_processed += 1

                events = detector.process_frame(frame_tracks)
                for ev in events:
                    all_events.append(ev)
                    if ev.event_type == EventType.ENTRY:
                        entry_count += 1
                    else:
                        exit_count += 1
                    gid = ev.payload.get("group_crossing_id")
                    if gid:
                        group_crossing_ids.add(str(gid))

                if self._detection_config.visualize or self._detection_config.save_annotated:
                    vis = draw_tracked_frame(
                        read_result.frame,
                        frame_tracks,
                        byte_tracker.history_store,
                        trajectory_length=self._detection_config.trajectory_length,
                    )
                    vis = draw_entry_exit_overlay(
                        vis,
                        lines,
                        recent_events=detector.recent_events,
                        debug=True,
                    )
                    if writer is not None:
                        writer.write(vis)
                    if self._detection_config.visualize:
                        if not show_frame(WINDOW_NAME, vis):
                            break

        if writer is not None:
            writer.close()

        summary = EntryExitRunSummary(
            video_path=path,
            frames_processed=frames_processed,
            entry_count=entry_count,
            exit_count=exit_count,
            group_crossings=len(group_crossing_ids),
        )
        logger.info("entry_exit_complete", **summary.__dict__)
        return summary, all_events

    def run_all(self, video_paths: list[Path]) -> list[tuple[EntryExitRunSummary, list[EventEnvelope]]]:
        results: list[tuple[EntryExitRunSummary, list[EventEnvelope]]] = []
        for path in video_paths:
            try:
                results.append(self.run_video(path))
            except FileNotFoundError:
                logger.error("video_not_found", path=str(path))
            except Exception as exc:
                logger.exception("entry_exit_failed", path=str(path), error=str(exc))
        return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Entry/exit line crossing detection")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--store-layout", type=Path, default=Path("configs/store_layout.yaml"))
    parser.add_argument("--camera-id", type=str, default="cam-entrance")
    parser.add_argument("--line-id", type=str, default=None, help="Only evaluate this line")
    parser.add_argument("--cooldown-frames", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--visualize", action="store_true")
    parser.add_argument("--save-annotated", action="store_true")
    parser.add_argument("--glob", type=str, default="*.mp4")
    parser.add_argument("--log-json", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--log-level", type=str, default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    configure_logging(json_logs=args.log_json, log_level=args.log_level)

    try:
        paths = iter_video_paths(args.source, args.glob)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("invalid_source", error=str(exc))
        return 1

    ee_settings = load_entry_exit_settings(args.store_layout)
    if args.line_id:
        ee_settings.active_line_id = args.line_id
    if args.cooldown_frames is not None:
        ee_settings.cooldown_frames = args.cooldown_frames

    det_config = DetectionConfig(
        video_source=args.source,
        output_dir=args.output_dir,
        visualize=args.visualize,
        save_annotated=args.save_annotated,
        video_glob=args.glob,
        log_json=args.log_json,
        log_level=args.log_level,
    )

    runner = EntryExitRunner(
        det_config,
        store_layout_path=args.store_layout,
        camera_id=args.camera_id,
        entry_exit_settings=ee_settings,
    )
    results = runner.run_all(paths)
    if not results:
        return 1

    total_entries = sum(s.entry_count for s, _ in results)
    total_exits = sum(s.exit_count for s, _ in results)
    logger.info("entry_exit_batch_done", entries=total_entries, exits=total_exits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
