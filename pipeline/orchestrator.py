"""
Full detection pipeline orchestrator — YOLOv8, ByteTrack, entry/exit, zones, sessions, Re-ID, JSONL.

Wires existing stage modules into one frame loop for `python -m pipeline.main run` and `pipeline/run.sh`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from shapely.geometry import Point, Polygon

from pipeline.config import DetectionConfig
from pipeline.detect import PersonDetector
from pipeline.emit import EmitSettings, StructuredEventEmitter
from pipeline.entry_exit import (
    EntryExitDetector,
    EntryExitSettings,
    VideoClock,
    load_entry_exit_settings,
    load_store_layout,
)
from pipeline.reid import ReIDSettings, ReentryCoordinator, load_reid_settings
from pipeline.session import SessionEngine, SessionSettings
from pipeline.settings import PipelineSettings
from pipeline.tracker import ByteTrackVisitorTracker
from pipeline.utils import FpsThrottle, VideoReader
from pipeline.queue_tracking import QueueTrackingEngine
from pipeline.zones import ZoneSettings, ZoneTrackingEngine
from schemas.challenge_events import format_visitor_id
from schemas.config import StaffAreaConfig, StoreLayoutConfig
from schemas.events import EventEnvelope, EventType
from shared.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineRunSummary:
    video_path: Path
    frames_processed: int
    events_emitted: int
    jsonl_path: Path | None
    unique_visitors: int


def _staff_polygon(area: StaffAreaConfig) -> Polygon:
    ring = [(float(p[0]), float(p[1])) for p in area.polygon]
    poly = Polygon(ring)
    return poly if poly.is_valid else poly.buffer(0)


def _is_staff_track(layout: StoreLayoutConfig, bbox: tuple[int, int, int, int]) -> bool:
    if not layout.staff_areas:
        return False
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    point = Point(cx, cy)
    for area in layout.staff_areas:
        if _staff_polygon(area).contains(point):
            return True
    return False


def _enrich_visitor_payload(event: EventEnvelope, session: SessionEngine) -> EventEnvelope:
    """Copy active visitor_id into payload after session state is updated."""
    if event.track_id is None:
        return event
    active = session.get_active_by_track(event.track_id)
    if active is None:
        return event
    payload = dict(event.payload)
    vid = format_visitor_id(active.visitor_id)
    payload["visitor_id"] = vid
    payload["session_sequence"] = active.session_sequence
    return event.model_copy(
        update={
            "payload": payload,
            "is_staff": active.is_staff,
            "global_person_id": vid,
        },
    )


class FullPipelineRunner:
    """
    End-to-end retail analytics pipeline for a single video source.

    Emits: entry, exit, reentry, zone_enter, zone_exit, zone_dwell (and position_snapshot when enabled).
    """

    def __init__(self, settings: PipelineSettings) -> None:
        self._settings = settings
        layout_path = Path(settings.store_layout_path)
        self._layout = load_store_layout(layout_path)
        self._detection = DetectionConfig(
            models_config_path=Path(settings.models_config_path),
            device="cuda:0" if settings.enable_gpu else "cpu",
            fps_limit=settings.pipeline_fps_limit or None,
        )
        self._entry_exit_settings = load_entry_exit_settings(layout_path)
        self._zone_settings = ZoneSettings(
            dwell_threshold_seconds=float(self._layout.dwell_threshold_seconds),
        )
        self._session_settings = SessionSettings()
        self._reid_settings = load_reid_settings(Path(settings.models_config_path))
        if settings.degrade_reid or not settings.enable_reid:
            self._reid_settings = self._reid_settings.model_copy(update={"enabled": False})

    def run(self, video_source: str | Path | None = None) -> PipelineRunSummary:
        source = Path(video_source or self._settings.pipeline_video_source)
        camera_id = self._settings.pipeline_camera_id
        store_id = self._layout.store_id or self._settings.pipeline_store_id

        emit_settings = EmitSettings(
            output_dir=Path(self._settings.events_output_dir),
            store_id=store_id,
            camera_id=camera_id,
        )
        emitter = StructuredEventEmitter(emit_settings)

        reader = VideoReader(
            source.expanduser().resolve(),
            max_consecutive_drops=self._detection.max_consecutive_drop_frames,
        )

        with reader:
            meta = reader.metadata
            clock = VideoClock(fps=meta.fps or 25.0)
            detector = PersonDetector(
                model_path=self._detection.resolved_model_path(),
                confidence=self._detection.resolved_confidence(),
                iou=self._detection.resolved_iou(),
                person_class_id=self._detection.resolved_person_class_id(),
                device=self._detection.device,
            )
            tracker = ByteTrackVisitorTracker(
                tracker_config=self._detection.load_tracker_yaml(),
                frame_rate=meta.fps or 25.0,
                track_timeout_frames=self._detection.resolved_track_timeout(),
                max_history_points=self._detection.track_history_max_points,
                group_entry_min_size=self._detection.group_entry_min_size,
            )
            entry_exit = EntryExitDetector(
                self._layout,
                self._entry_exit_settings,
                camera_id=camera_id,
                clock=clock,
            )
            zones = ZoneTrackingEngine(
                self._layout,
                self._zone_settings,
                camera_id=camera_id,
                clock=clock,
            )
            queues = QueueTrackingEngine(
                self._layout,
                camera_id=camera_id,
                clock=clock,
            )
            session = SessionEngine(store_id, self._session_settings)
            reentry_coord: ReentryCoordinator | None = None
            if self._reid_settings.enabled:
                reentry_coord = ReentryCoordinator(
                    store_id=store_id,
                    camera_id=camera_id,
                    clock=clock,
                    settings=self._reid_settings,
                )

            throttle = FpsThrottle(self._detection.fps_limit)
            frames_processed = 0
            events_emitted = 0

            for read_result in reader.frames():
                if read_result.dropped or read_result.frame is None:
                    continue
                throttle.wait()
                frame = read_result.frame
                frame_index = read_result.frame_index

                detections = detector.detect_frame(frame, frame_index)
                frame_tracks = tracker.update(detections)

                frame_events: list[EventEnvelope] = []
                frame_events.extend(entry_exit.process_frame(frame_tracks))
                frame_events.extend(zones.process_frame(frame_tracks))
                frame_events.extend(queues.process_frame(frame_tracks))

                processed: list[EventEnvelope] = []
                for raw in frame_events:
                    bbox = raw.bbox
                    if bbox:
                        raw = raw.model_copy(update={"is_staff": _is_staff_track(self._layout, bbox)})

                    if (
                        raw.event_type == EventType.ENTRY
                        and reentry_coord is not None
                        and bbox is not None
                    ):
                        reentry_ev, match = reentry_coord.check_reentry(
                            frame=frame,
                            bbox_xyxy=bbox,
                            track_id=raw.track_id or 0,
                            frame_index=frame_index,
                            occurred_at=raw.occurred_at,
                            entry_line_id=str(raw.payload.get("line_id") or ""),
                        )
                        if reentry_ev is not None and match is not None:
                            reentry_ev = reentry_ev.model_copy(
                                update={"is_staff": raw.is_staff},
                            )
                            session.process_event(reentry_ev)
                            processed.append(_enrich_visitor_payload(reentry_ev, session))
                            continue

                    completed = session.process_event(raw)
                    if raw.event_type == EventType.EXIT and completed and reentry_coord:
                        reentry_coord.on_visitor_exit(
                            visitor_id=completed.visitor_id,
                            frame=frame,
                            bbox_xyxy=bbox,
                            exit_event=raw,
                        )
                    processed.append(_enrich_visitor_payload(raw, session))

                for ev in processed:
                    if emitter.emit(ev):
                        events_emitted += 1

                frames_processed += 1

            session.close_all_active(at=clock.timestamp_for_frame(frames_processed))
            emitter.flush()

        jsonl_path = emitter.current_log_path
        logger.info(
            "pipeline_run_complete",
            video=str(source),
            frames=frames_processed,
            events=events_emitted,
            jsonl=str(jsonl_path) if jsonl_path else None,
            visitors=len(session.completed_sessions) + len(session.active_sessions),
        )

        return PipelineRunSummary(
            video_path=source,
            frames_processed=frames_processed,
            events_emitted=events_emitted,
            jsonl_path=jsonl_path,
            unique_visitors=len(session.completed_sessions) + len(session.active_sessions),
        )
