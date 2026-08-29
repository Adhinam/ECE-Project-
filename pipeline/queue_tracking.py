"""Billing queue tracking — join, abandon, and depth events from layout queue polygons."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shapely.geometry import Point, Polygon

from pipeline.entry_exit import VideoClock
from pipeline.tracker import FrameTracks, TrackedVisitor
from schemas.config import QueueConfig, StoreLayoutConfig
from schemas.events import EventEnvelope, EventType, generate_event_id
from shared.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class _QueuePolygon:
    queue_id: str
    polygon: Polygon
    max_capacity: int


@dataclass
class _QueueMembership:
    queue_id: str
    track_id: int
    joined_frame: int
    session_sequence: int
    inside: bool = True


class QueueTrackingEngine:
    """
    Emit QUEUE_JOIN / QUEUE_LEAVE (billing abandon) and periodic QUEUE_DEPTH.

    Uses `StoreLayoutConfig.queues`; if none are configured, falls back to the
    checkout zone polygon when present (id `checkout` or first zone named checkout).
    """

    def __init__(
        self,
        layout: StoreLayoutConfig,
        *,
        camera_id: str,
        clock: VideoClock,
        depth_emit_interval_frames: int = 30,
    ) -> None:
        self._layout = layout
        self._camera_id = camera_id
        self._clock = clock
        self._depth_interval = max(1, depth_emit_interval_frames)
        self._queues = self._load_queue_polygons(layout)
        self._members: dict[tuple[int, str], _QueueMembership] = {}
        self._seq: dict[tuple[int, str], int] = {}
        self._last_depth_frame = -self._depth_interval
        if not self._queues:
            logger.warning("queue_tracking_no_queues", store_id=layout.store_id)

    @staticmethod
    def _polygon_from_points(points: list[list[int]]) -> Polygon:
        ring = [(float(p[0]), float(p[1])) for p in points]
        poly = Polygon(ring)
        return poly if poly.is_valid else poly.buffer(0)

    def _load_queue_polygons(self, layout: StoreLayoutConfig) -> list[_QueuePolygon]:
        queues: list[_QueuePolygon] = []
        for q in layout.queues:
            queues.append(
                _QueuePolygon(
                    queue_id=q.id,
                    polygon=self._polygon_from_points(q.polygon),
                    max_capacity=q.max_capacity,
                )
            )
        if queues:
            return queues
        checkout = next((z for z in layout.zones if z.id in ("checkout", "billing")), None)
        if checkout is not None:
            queues.append(
                _QueuePolygon(
                    queue_id="checkout-1",
                    polygon=self._polygon_from_points(checkout.polygon),
                    max_capacity=20,
                )
            )
        return queues

    def _next_sequence(self, track_id: int, queue_id: str) -> int:
        key = (track_id, queue_id)
        seq = self._seq.get(key, 0) + 1
        self._seq[key] = seq
        return seq

    def _centroid_in_queue(self, track: TrackedVisitor, queue: _QueuePolygon) -> bool:
        cx = (track.bbox_xyxy[0] + track.bbox_xyxy[2]) / 2
        cy = (track.bbox_xyxy[1] + track.bbox_xyxy[3]) / 2
        return queue.polygon.contains(Point(cx, cy))

    def _envelope(
        self,
        *,
        event_type: EventType,
        track_id: int,
        queue_id: str,
        frame_index: int,
        confidence: float,
        bbox: tuple[int, int, int, int],
        payload: dict[str, Any],
    ) -> EventEnvelope:
        seq = payload.get("session_sequence", 0)
        return EventEnvelope(
            event_id=generate_event_id(
                store_id=self._layout.store_id,
                camera_id=self._camera_id,
                event_type=event_type.value,
                track_id=track_id,
                frame_index=frame_index,
                subtype=f"{queue_id}:q{seq}",
            ),
            event_type=event_type,
            store_id=self._layout.store_id,
            camera_id=self._camera_id,
            occurred_at=self._clock.timestamp_for_frame(frame_index),
            track_id=track_id,
            global_person_id=f"track-{track_id}",
            is_staff=False,
            confidence=min(1.0, max(0.5, confidence)),
            calibration_method="queue_polygon_v1",
            bbox=bbox,
            frame_index=frame_index,
            payload={"queue_id": queue_id, **payload},
        )

    def process_frame(self, frame_tracks: FrameTracks) -> list[EventEnvelope]:
        if not self._queues:
            return []

        frame_index = frame_tracks.frame_index
        events: list[EventEnvelope] = []
        active_keys: set[tuple[int, str]] = set()

        for track in frame_tracks.tracks:
            for queue in self._queues:
                key = (track.track_id, queue.queue_id)
                inside = self._centroid_in_queue(track, queue)
                if inside:
                    active_keys.add(key)
                    member = self._members.get(key)
                    if member is None or not member.inside:
                        seq = self._next_sequence(track.track_id, queue.queue_id)
                        self._members[key] = _QueueMembership(
                            queue_id=queue.queue_id,
                            track_id=track.track_id,
                            joined_frame=frame_index,
                            session_sequence=seq,
                            inside=True,
                        )
                        depth = self._depth_for_queue(queue.queue_id, frame_tracks)
                        events.append(
                            self._envelope(
                                event_type=EventType.QUEUE_JOIN,
                                track_id=track.track_id,
                                queue_id=queue.queue_id,
                                frame_index=frame_index,
                                confidence=track.confidence,
                                bbox=track.bbox_xyxy,
                                payload={
                                    "session_sequence": seq,
                                    "queue_depth": depth,
                                    "billing_queue": True,
                                },
                            )
                        )
                    else:
                        member.inside = True
                elif key in self._members and self._members[key].inside:
                    member = self._members[key]
                    member.inside = False
                    events.append(
                        self._envelope(
                            event_type=EventType.QUEUE_LEAVE,
                            track_id=track.track_id,
                            queue_id=queue.queue_id,
                            frame_index=frame_index,
                            confidence=track.confidence,
                            bbox=track.bbox_xyxy,
                            payload={
                                "session_sequence": member.session_sequence,
                                "billing_queue_abandon": True,
                                "dwell_seconds": max(
                                    0.0,
                                    (frame_index - member.joined_frame)
                                    / max(self._clock.fps, 1.0),
                                ),
                            },
                        )
                    )

        if frame_index - self._last_depth_frame >= self._depth_interval:
            self._last_depth_frame = frame_index
            for queue in self._queues:
                depth = self._depth_for_queue(queue.queue_id, frame_tracks)
                events.append(
                    EventEnvelope(
                        event_id=generate_event_id(
                            store_id=self._layout.store_id,
                            camera_id=self._camera_id,
                            event_type=EventType.QUEUE_DEPTH.value,
                            track_id=0,
                            frame_index=frame_index,
                            subtype=f"depth:{queue.queue_id}",
                        ),
                        event_type=EventType.QUEUE_DEPTH,
                        store_id=self._layout.store_id,
                        camera_id=self._camera_id,
                        occurred_at=self._clock.timestamp_for_frame(frame_index),
                        track_id=None,
                        confidence=1.0,
                        calibration_method="queue_polygon_v1",
                        frame_index=frame_index,
                        payload={
                            "queue_id": queue.queue_id,
                            "queue_depth": depth,
                        },
                    )
                )

        return events

    def _depth_for_queue(self, queue_id: str, frame_tracks: FrameTracks) -> int:
        queue = next((q for q in self._queues if q.queue_id == queue_id), None)
        if queue is None:
            return 0
        count = 0
        for track in frame_tracks.tracks:
            if self._centroid_in_queue(track, queue):
                count += 1
        return count
