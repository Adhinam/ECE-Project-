"""Heatmap analytics — aggregate spatial samples into grid cells."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from api.domain.heatmap import normalize_to_cell
from app.repositories import HeatmapRepository
from app.state import AppState
from schemas.api import HeatmapCell, HeatmapResponse, MetricConfidence
from shared.logging import get_logger

logger = get_logger(__name__)

DEFAULT_FRAME_WIDTH = 1920
DEFAULT_FRAME_HEIGHT = 1080

MIN_SAMPLES_HIGH = 10
MIN_SAMPLES_MEDIUM = 3


def _heatmap_confidence(sample_count: int) -> MetricConfidence:
    if sample_count >= MIN_SAMPLES_HIGH:
        return MetricConfidence.HIGH
    if sample_count >= MIN_SAMPLES_MEDIUM:
        return MetricConfidence.MEDIUM
    if sample_count > 0:
        return MetricConfidence.LOW
    return MetricConfidence.UNAVAILABLE


def _normalize_visits(cells: list[HeatmapCell]) -> list[HeatmapCell]:
    peak = max((c.visits for c in cells), default=0)
    if peak <= 0:
        return cells
    return [
        c.model_copy(
            update={"visits_normalized": int(round(100.0 * c.visits / peak))},
        )
        for c in cells
    ]


class HeatmapEngine:
    def __init__(
        self,
        session: AsyncSession,
        app_state: AppState,
        *,
        frame_width: int = DEFAULT_FRAME_WIDTH,
        frame_height: int = DEFAULT_FRAME_HEIGHT,
    ) -> None:
        self._repo = HeatmapRepository(session)
        self._state = app_state
        self._frame_width = frame_width
        self._frame_height = frame_height

    async def get_heatmap(
        self,
        store_id: str,
        from_time: datetime,
        to_time: datetime,
        resolution: int,
    ) -> HeatmapResponse:
        if not self._state.db_available:
            logger.warning("heatmap_degraded_no_db", store_id=store_id)
            return HeatmapResponse(
                store_id=store_id,
                resolution=resolution,
                from_time=from_time,
                to_time=to_time,
                cells=[],
                data_confidence=MetricConfidence.UNAVAILABLE,
            )

        samples = await self._repo.fetch_spatial_samples(store_id, from_time, to_time)
        visits: dict[tuple[int, int], int] = defaultdict(int)
        dwell: dict[tuple[int, int], float] = defaultdict(float)

        for x, y, dwell_seconds in samples:
            if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
                px = x * self._frame_width
                py = y * self._frame_height
            else:
                px, py = x, y
            cell_x, cell_y = normalize_to_cell(
                px,
                py,
                frame_width=self._frame_width,
                frame_height=self._frame_height,
                resolution=resolution,
            )
            visits[(cell_x, cell_y)] += 1
            dwell[(cell_x, cell_y)] += dwell_seconds

        cells = [
            HeatmapCell(
                x=cx,
                y=cy,
                visits=visits[(cx, cy)],
                dwell_seconds=round(dwell[(cx, cy)], 2),
            )
            for (cx, cy) in sorted(visits.keys())
        ]
        cells = _normalize_visits(cells)
        sample_count = len(samples)

        return HeatmapResponse(
            store_id=store_id,
            resolution=resolution,
            from_time=from_time,
            to_time=to_time,
            cells=cells,
            data_confidence=_heatmap_confidence(sample_count),
        )
