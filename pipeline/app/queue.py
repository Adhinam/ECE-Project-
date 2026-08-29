"""Queue depth and wait-time estimation."""

from __future__ import annotations

from pipeline.app.geometry import point_in_polygon
from pipeline.app.interfaces import Track
from schemas.config import StoreLayoutConfig
from schemas.events import EventEnvelope


class QueueDetector:
    def __init__(self, layout: StoreLayoutConfig) -> None:
        self._layout = layout

    def depth(self, tracks: list[Track], queue_id: str) -> int:
        queue = next((q for q in self._layout.queues if q.id == queue_id), None)
        if queue is None:
            return 0
        count = 0
        for t in tracks:
            if t.is_staff:
                continue
            cx = (t.bbox[0] + t.bbox[2]) / 2
            cy = (t.bbox[1] + t.bbox[3]) / 2
            if point_in_polygon(cx, cy, queue.polygon):
                count += 1
        return count

    def periodic_depth_events(
        self, tracks: list[Track], *, store_id: str, camera_id: str
    ) -> list[EventEnvelope]:
        _ = (store_id, camera_id)
        return []
