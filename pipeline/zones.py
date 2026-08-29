"""Zone tracking engine — Shapely polygons, dwell sessions, occlusion tolerance."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from shapely.geometry import Point, Polygon

from pipeline.config import DetectionConfig
from pipeline.entry_exit import VideoClock
from pipeline.tracker import ByteTrackVisitorTracker, FrameTracks, TrackingRunner, draw_tracked_frame
from pipeline.utils import (
    AnnotatedVideoWriter,
    FpsThrottle,
    VideoReader,
    ensure_directory,
    iter_video_paths,
    show_frame,
)
from schemas.config import StoreLayoutConfig, ZoneConfig
from schemas.events import EventEnvelope, EventType, generate_event_id
from shared.logging import configure_logging, get_logger

logger = get_logger(__name__)

WINDOW_NAME = "store-intelligence-zones"


class ZoneSettings(BaseSettings):
    """Runtime tuning for zone tracking (overrides layout defaults)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    dwell_threshold_seconds: float = Field(
        default=30.0,
        ge=0.0,
        description="Seconds inside zone before first ZONE_DWELL",
    )
    dwell_emit_interval_seconds: float = Field(
        default=30.0,
        ge=1.0,
        description="Emit ZONE_DWELL every N seconds while still inside",
    )
    occlusion_grace_frames: int = Field(
        default=15,
        ge=0,
        description="Frames a track may be missing before ZONE_EXIT",
    )
    zone_confidence_floor: float = Field(default=0.5, ge=0.0, le=1.0)
    calibration_method: str = "threshold_v1"
    active_zone_id: str | None = Field(
        default=None,
        description="If set, only evaluate this zone",
    )


@dataclass(frozen=True)
class ZonePolygon:
    """Shapely-backed zone with stable id and overlap priority (smaller area = higher)."""

    zone_id: str
    polygon: Polygon
    area: float
    priority: int  # lower = evaluated first for overlapping viz

    def contains(self, x: float, y: float) -> bool:
        return self.polygon.contains(Point(x, y))

    @property
    def exterior_coords(self) -> list[tuple[int, int]]:
        return [(int(x), int(y)) for x, y in self.polygon.exterior.coords]


def _polygon_from_config(cfg: ZoneConfig) -> Polygon:
    if len(cfg.polygon) < 3:
        raise ValueError(f"Zone {cfg.id} needs at least 3 polygon points")
    ring = [(float(p[0]), float(p[1])) for p in cfg.polygon]
    poly = Polygon(ring)
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly


def load_zone_polygons(
    layout: StoreLayoutConfig,
    *,
    active_zone_id: str | None = None,
) -> list[ZonePolygon]:
    """Build zone list from layout; sorted by area for overlap handling."""
    zones: list[ZonePolygon] = []
    for zc in layout.zones:
        if active_zone_id is not None and zc.id != active_zone_id:
            continue
        poly = _polygon_from_config(zc)
        zones.append(
            ZonePolygon(
                zone_id=zc.id,
                polygon=poly,
                area=float(poly.area),
                priority=0,
            )
        )
    zones.sort(key=lambda z: z.area)
    return [
        ZonePolygon(zone_id=z.zone_id, polygon=z.polygon, area=z.area, priority=i)
        for i, z in enumerate(zones)
    ]


def zones_for_point(x: float, y: float, zones: list[ZonePolygon]) -> list[str]:
    """
    Return all zone ids containing (x, y).

    Overlapping zones: visitor is considered inside every matching polygon.
    """
    return [z.zone_id for z in zones if z.contains(x, y)]


@dataclass
class ZoneVisitRecord:
    """Completed or active visit to one zone."""

    zone_id: str
    session_sequence: int
    entered_frame: int
    exited_frame: int | None = None
    dwell_emit_count: int = 0
    total_dwell_seconds: float = 0.0


@dataclass
class VisitorZoneHistory:
    """Per-track zone visit history across the video session."""

    track_id: int
    visits: list[ZoneVisitRecord] = field(default_factory=list)

    def add_visit(self, record: ZoneVisitRecord) -> None:
        self.visits.append(record)

    def active_visit(self, zone_id: str) -> ZoneVisitRecord | None:
        for v in reversed(self.visits):
            if v.zone_id == zone_id and v.exited_frame is None:
                return v
        return None


@dataclass
class ZoneSession:
    """Active (track, zone) session — supports occlusion grace and periodic dwell."""

    zone_id: str
    track_id: int
    session_sequence: int
    entered_frame: int
    entered_at: datetime
    last_seen_frame: int
    last_seen_at: datetime
    missed_frames: int = 0
    last_dwell_emit_at: datetime | None = None
    first_dwell_emitted: bool = False
    confidence_sum: float = 0.0
    confidence_count: int = 0
    inside: bool = True

    @property
    def avg_confidence(self) -> float:
        if self.confidence_count == 0:
            return 0.0
        return self.confidence_sum / self.confidence_count

    def dwell_seconds(self, now: datetime) -> float:
        return max(0.0, (now - self.entered_at).total_seconds())


class ZoneTrackingEngine:
    """
    Multi-zone tracker with dwell emission and occlusion tolerance.

    Emits ZONE_ENTER, ZONE_EXIT, and ZONE_DWELL (every `dwell_emit_interval_seconds`
    after initial `dwell_threshold_seconds`).
    """

    def __init__(
        self,
        layout: StoreLayoutConfig,
        settings: ZoneSettings,
        *,
        camera_id: str,
        clock: VideoClock,
    ) -> None:
        self._layout = layout
        self._settings = settings
        self._camera_id = camera_id
        self._clock = clock
        self._zones = load_zone_polygons(layout, active_zone_id=settings.active_zone_id)
        self._sessions: dict[tuple[int, str], ZoneSession] = {}
        self._histories: dict[int, VisitorZoneHistory] = {}
        self._next_session_seq: dict[tuple[int, str], int] = {}

        if not self._zones:
            logger.warning("zone_tracking_no_zones", store_id=layout.store_id)

    @property
    def zones(self) -> list[ZonePolygon]:
        return list(self._zones)

    @property
    def histories(self) -> dict[int, VisitorZoneHistory]:
        return self._histories

    def active_zone_counts(self) -> dict[str, int]:
        """Current visitor count per zone (for visualization)."""
        counts: dict[str, int] = {}
        for session in self._sessions.values():
            if session.inside:
                counts[session.zone_id] = counts.get(session.zone_id, 0) + 1
        return counts

    def _history(self, track_id: int) -> VisitorZoneHistory:
        if track_id not in self._histories:
            self._histories[track_id] = VisitorZoneHistory(track_id=track_id)
        return self._histories[track_id]

    def _next_sequence(self, track_id: int, zone_id: str) -> int:
        key = (track_id, zone_id)
        seq = self._next_session_seq.get(key, 0) + 1
        self._next_session_seq[key] = seq
        return seq

    def _confidence(self, raw: float) -> float:
        return min(1.0, max(self._settings.zone_confidence_floor, raw))

    def _to_envelope(
        self,
        *,
        event_type: EventType,
        track_id: int,
        zone_id: str,
        session_sequence: int,
        frame_index: int,
        confidence: float,
        bbox: tuple[int, int, int, int] | None,
        payload: dict[str, Any],
    ) -> EventEnvelope:
        return EventEnvelope(
            event_id=generate_event_id(
                store_id=self._layout.store_id,
                camera_id=self._camera_id,
                event_type=event_type.value,
                track_id=track_id,
                frame_index=frame_index,
                subtype=f"{zone_id}:s{session_sequence}",
            ),
            event_type=event_type,
            store_id=self._layout.store_id,
            camera_id=self._camera_id,
            occurred_at=self._clock.timestamp_for_frame(frame_index),
            track_id=track_id,
            global_person_id=f"track-{track_id}",
            is_staff=False,
            confidence=self._confidence(confidence),
            calibration_method=self._settings.calibration_method,
            bbox=bbox,
            frame_index=frame_index,
            payload={
                "zone_id": zone_id,
                "session_sequence": session_sequence,
                **payload,
            },
        )

    def _start_session(
        self,
        track_id: int,
        zone_id: str,
        frame_index: int,
        confidence: float,
        bbox: tuple[int, int, int, int],
        centroid: tuple[float, float],
    ) -> tuple[ZoneSession, EventEnvelope]:
        now = self._clock.timestamp_for_frame(frame_index)
        seq = self._next_sequence(track_id, zone_id)
        session = ZoneSession(
            zone_id=zone_id,
            track_id=track_id,
            session_sequence=seq,
            entered_frame=frame_index,
            entered_at=now,
            last_seen_frame=frame_index,
            last_seen_at=now,
            confidence_sum=confidence,
            confidence_count=1,
        )
        self._sessions[(track_id, zone_id)] = session
        history = self._history(track_id)
        history.add_visit(
            ZoneVisitRecord(
                zone_id=zone_id,
                session_sequence=seq,
                entered_frame=frame_index,
            )
        )
        event = self._to_envelope(
            event_type=EventType.ZONE_ENTER,
            track_id=track_id,
            zone_id=zone_id,
            session_sequence=seq,
            frame_index=frame_index,
            confidence=confidence,
            bbox=bbox,
            payload={
                "centroid": [centroid[0], centroid[1]],
                "overlapping_zones": zones_for_point(centroid[0], centroid[1], self._zones),
            },
        )
        logger.info(
            "zone_enter",
            track_id=track_id,
            zone_id=zone_id,
            session_sequence=seq,
            frame_index=frame_index,
        )
        return session, event

    def _end_session(
        self,
        session: ZoneSession,
        frame_index: int,
        *,
        reason: str,
    ) -> EventEnvelope:
        session.inside = False
        now = self._clock.timestamp_for_frame(frame_index)
        dwell_sec = session.dwell_seconds(now)
        key = (session.track_id, session.zone_id)
        self._sessions.pop(key, None)

        visit = self._history(session.track_id).active_visit(session.zone_id)
        if visit is not None:
            visit.exited_frame = frame_index
            visit.total_dwell_seconds = dwell_sec

        event = self._to_envelope(
            event_type=EventType.ZONE_EXIT,
            track_id=session.track_id,
            zone_id=session.zone_id,
            session_sequence=session.session_sequence,
            frame_index=frame_index,
            confidence=session.avg_confidence,
            bbox=None,
            payload={
                "dwell_seconds": round(dwell_sec, 2),
                "exit_reason": reason,
            },
        )
        logger.info(
            "zone_exit",
            track_id=session.track_id,
            zone_id=session.zone_id,
            session_sequence=session.session_sequence,
            frame_index=frame_index,
            reason=reason,
            dwell_seconds=round(dwell_sec, 2),
        )
        return event

    def _maybe_emit_dwell(
        self,
        session: ZoneSession,
        frame_index: int,
        bbox: tuple[int, int, int, int] | None,
    ) -> EventEnvelope | None:
        now = self._clock.timestamp_for_frame(frame_index)
        elapsed = session.dwell_seconds(now)

        if elapsed < self._settings.dwell_threshold_seconds:
            return None

        is_first_dwell = not session.first_dwell_emitted
        if is_first_dwell:
            session.first_dwell_emitted = True
            session.last_dwell_emit_at = now
        else:
            if session.last_dwell_emit_at is None:
                session.last_dwell_emit_at = now
                return None
            since_last = (now - session.last_dwell_emit_at).total_seconds()
            if since_last < self._settings.dwell_emit_interval_seconds:
                return None
            session.last_dwell_emit_at = now

        visit = self._history(session.track_id).active_visit(session.zone_id)
        if visit is not None:
            visit.dwell_emit_count += 1

        return self._to_envelope(
            event_type=EventType.ZONE_DWELL,
            track_id=session.track_id,
            zone_id=session.zone_id,
            session_sequence=session.session_sequence,
            frame_index=frame_index,
            confidence=session.avg_confidence,
            bbox=bbox,
            payload={
                "dwell_seconds": round(elapsed, 2),
                "periodic": not is_first_dwell,
            },
        )

    def process_frame(self, frame_tracks: FrameTracks) -> list[EventEnvelope]:
        """Update zone state for all tracks in the frame; return new events."""
        frame_index = frame_tracks.frame_index
        events: list[EventEnvelope] = []
        seen_track_ids: set[int] = set()

        for visitor in frame_tracks.tracks:
            seen_track_ids.add(visitor.track_id)
            cx, cy = visitor.centroid
            inside_now = set(zones_for_point(cx, cy, self._zones))

            for zone in self._zones:
                key = (visitor.track_id, zone.zone_id)
                session = self._sessions.get(key)
                is_inside = zone.zone_id in inside_now

                if session is None and is_inside:
                    _, enter_ev = self._start_session(
                        visitor.track_id,
                        zone.zone_id,
                        frame_index,
                        visitor.confidence,
                        visitor.bbox_xyxy,
                        visitor.centroid,
                    )
                    events.append(enter_ev)
                    session = self._sessions[key]

                if session is not None:
                    session.missed_frames = 0
                    session.last_seen_frame = frame_index
                    session.last_seen_at = self._clock.timestamp_for_frame(frame_index)
                    session.confidence_sum += visitor.confidence
                    session.confidence_count += 1

                    if is_inside:
                        dwell_ev = self._maybe_emit_dwell(
                            session, frame_index, visitor.bbox_xyxy
                        )
                        if dwell_ev is not None:
                            events.append(dwell_ev)
                            logger.debug(
                                "zone_dwell",
                                track_id=visitor.track_id,
                                zone_id=zone.zone_id,
                                dwell_seconds=dwell_ev.payload.get("dwell_seconds"),
                            )
                    else:
                        events.append(
                            self._end_session(session, frame_index, reason="centroid_outside")
                        )

        # Occlusion: tracks not seen this frame
        grace = self._settings.occlusion_grace_frames
        to_close: list[ZoneSession] = []
        for key, session in list(self._sessions.items()):
            track_id, zone_id = key
            if track_id in seen_track_ids:
                continue
            session.missed_frames += 1
            if session.missed_frames > grace:
                to_close.append(session)

        for session in to_close:
            events.append(
                self._end_session(
                    session,
                    frame_index,
                    reason="occlusion_timeout",
                )
            )

        return events


def load_zone_settings(layout_path: Path, layout: StoreLayoutConfig) -> ZoneSettings:
    """Merge optional `zones` block from layout file with defaults."""
    settings = ZoneSettings(dwell_threshold_seconds=float(layout.dwell_threshold_seconds))
    if not layout_path.is_file():
        return settings
    suffix = layout_path.suffix.lower()
    with layout_path.open(encoding="utf-8") as fh:
        if suffix == ".json":
            data = json.load(fh) or {}
        else:
            data = yaml.safe_load(fh) or {}
    block = data.get("zones_config") or data.get("zone_tracking")
    if block:
        settings = ZoneSettings.model_validate({**settings.model_dump(), **block})
    return settings


def load_layout_file(path: Path) -> StoreLayoutConfig:
    """Load store layout from YAML or JSON (`store_layout.json` / `.yaml`)."""
    if not path.is_file():
        raise FileNotFoundError(f"Store layout not found: {path.resolve()}")
    suffix = path.suffix.lower()
    with path.open(encoding="utf-8") as fh:
        if suffix == ".json":
            data = json.load(fh)
        else:
            data = yaml.safe_load(fh)
    return StoreLayoutConfig.model_validate(data or {})


def draw_zone_overlay(
    frame: np.ndarray,
    zones: list[ZonePolygon],
    *,
    active_by_zone: dict[str, int] | None = None,
    debug: bool = True,
) -> np.ndarray:
    """
    Draw zone polygons and optional occupancy counts.

    Args:
        active_by_zone: zone_id -> count of visitors currently inside
    """
    overlay = frame.copy()
    active_by_zone = active_by_zone or {}
    palette = [
        (255, 128, 0),
        (0, 200, 255),
        (200, 100, 255),
        (100, 255, 100),
    ]

    for z in zones:
        color = palette[z.priority % len(palette)]
        pts = np.array(z.exterior_coords, dtype=np.int32)
        cv2.fillPoly(overlay, [pts], color)

    blended = cv2.addWeighted(overlay, 0.22, frame, 0.78, 0)
    for z in zones:
        color = palette[z.priority % len(palette)]
        pts = np.array(z.exterior_coords, dtype=np.int32)
        cv2.polylines(blended, [pts], isClosed=True, color=color, thickness=2)
        if debug:
            cx = int(sum(p[0] for p in z.exterior_coords) / len(z.exterior_coords))
            cy = int(sum(p[1] for p in z.exterior_coords) / len(z.exterior_coords))
            count = active_by_zone.get(z.zone_id, 0)
            label = f"{z.zone_id} ({count})"
            cv2.putText(
                blended,
                label,
                (cx - 40, cy),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                2,
                cv2.LINE_AA,
            )
    return blended


@dataclass
class ZoneRunSummary:
    video_path: Path
    frames_processed: int
    zone_enters: int
    zone_exits: int
    zone_dwells: int


class ZoneTrackingRunner:
    """Full pipeline: detect → track → zone events."""

    def __init__(
        self,
        detection_config: DetectionConfig,
        *,
        store_layout_path: Path,
        camera_id: str = "cam-entrance",
        zone_settings: ZoneSettings | None = None,
    ) -> None:
        self._detection_config = detection_config
        self._layout_path = store_layout_path
        self._layout = load_layout_file(store_layout_path)
        self._zone_settings = zone_settings or load_zone_settings(
            store_layout_path, self._layout
        )
        self._camera_id = camera_id
        self._tracking = TrackingRunner(detection_config)

    def run_video(self, video_path: Path) -> tuple[ZoneRunSummary, list[EventEnvelope]]:
        path = video_path.expanduser().resolve()
        logger.info("zone_tracking_start", path=str(path), store_id=self._layout.store_id)

        enters = exits = dwells = 0
        frames_processed = 0
        all_events: list[EventEnvelope] = []

        output_dir = ensure_directory(self._detection_config.output_dir)
        writer: AnnotatedVideoWriter | None = None

        reader = VideoReader(
            path=path,
            max_consecutive_drops=self._detection_config.max_consecutive_drop_frames,
        )

        with reader:
            meta = reader.metadata
            clock = VideoClock(fps=meta.fps or 30.0)
            engine = ZoneTrackingEngine(
                self._layout,
                self._zone_settings,
                camera_id=self._camera_id,
                clock=clock,
            )
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
                out_path = output_dir / f"{path.stem}_zones.mp4"
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

                events = engine.process_frame(frame_tracks)
                for ev in events:
                    all_events.append(ev)
                    if ev.event_type == EventType.ZONE_ENTER:
                        enters += 1
                    elif ev.event_type == EventType.ZONE_EXIT:
                        exits += 1
                    elif ev.event_type == EventType.ZONE_DWELL:
                        dwells += 1

                if self._detection_config.visualize or self._detection_config.save_annotated:
                    vis = draw_tracked_frame(
                        read_result.frame,
                        frame_tracks,
                        byte_tracker.history_store,
                        trajectory_length=self._detection_config.trajectory_length,
                    )
                    vis = draw_zone_overlay(
                        vis,
                        engine.zones,
                        active_by_zone=engine.active_zone_counts(),
                        debug=True,
                    )
                    if writer is not None:
                        writer.write(vis)
                    if self._detection_config.visualize:
                        if not show_frame(WINDOW_NAME, vis):
                            break

        if writer is not None:
            writer.close()

        summary = ZoneRunSummary(
            video_path=path,
            frames_processed=frames_processed,
            zone_enters=enters,
            zone_exits=exits,
            zone_dwells=dwells,
        )
        logger.info("zone_tracking_complete", **summary.__dict__)
        return summary, all_events

    def run_all(self, paths: list[Path]) -> list[tuple[ZoneRunSummary, list[EventEnvelope]]]:
        results: list[tuple[ZoneRunSummary, list[EventEnvelope]]] = []
        for path in paths:
            try:
                results.append(self.run_video(path))
            except Exception as exc:
                logger.exception("zone_tracking_failed", path=str(path), error=str(exc))
        return results


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Zone enter/exit/dwell tracking")
    parser.add_argument(
        "--store-layout",
        type=Path,
        default=Path("configs/store_layout.yaml"),
        help="store_layout.yaml or store_layout.json",
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--camera-id", type=str, default="cam-entrance")
    parser.add_argument("--zone-id", type=str, default=None)
    parser.add_argument("--dwell-threshold", type=float, default=None)
    parser.add_argument("--dwell-interval", type=float, default=None)
    parser.add_argument("--occlusion-grace", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--visualize", action="store_true")
    parser.add_argument("--save-annotated", action="store_true")
    parser.add_argument("--glob", type=str, default="*.mp4")
    parser.add_argument("--log-json", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--log-level", type=str, default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    configure_logging(json_logs=args.log_json, log_level=args.log_level)

    try:
        paths = iter_video_paths(args.source, args.glob)
        layout = load_layout_file(args.store_layout)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("zone_setup_failed", error=str(exc))
        return 1

    settings = load_zone_settings(args.store_layout, layout)
    if args.zone_id:
        settings.active_zone_id = args.zone_id
    if args.dwell_threshold is not None:
        settings.dwell_threshold_seconds = args.dwell_threshold
    if args.dwell_interval is not None:
        settings.dwell_emit_interval_seconds = args.dwell_interval
    if args.occlusion_grace is not None:
        settings.occlusion_grace_frames = args.occlusion_grace

    det = DetectionConfig(
        video_source=args.source,
        output_dir=args.output_dir,
        visualize=args.visualize,
        save_annotated=args.save_annotated,
        video_glob=args.glob,
        log_json=args.log_json,
        log_level=args.log_level,
    )
    runner = ZoneTrackingRunner(
        det,
        store_layout_path=args.store_layout,
        camera_id=args.camera_id,
        zone_settings=settings,
    )
    results = runner.run_all(paths)
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
