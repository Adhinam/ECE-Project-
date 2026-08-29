"""Staff exclusion — zone-based or heuristic classifier."""

from __future__ import annotations

from pipeline.app.geometry import point_in_polygon
from pipeline.app.interfaces import Track
from schemas.config import StoreLayoutConfig
from pipeline.settings import PipelineSettings


class StaffClassifier:
    def __init__(self, layout: StoreLayoutConfig, settings: PipelineSettings) -> None:
        self._layout = layout
        self._settings = settings

    def is_staff(self, track: Track) -> bool:
        if self._settings.degrade_staff_classifier:
            return False
        cx = (track.bbox[0] + track.bbox[2]) / 2
        cy = (track.bbox[1] + track.bbox[3]) / 2
        for area in self._layout.staff_areas:
            if point_in_polygon(cx, cy, area.polygon):
                return True
        return False
