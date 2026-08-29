"""Heatmap aggregation — grid cell binning."""

from __future__ import annotations

from schemas.api import HeatmapCell


def normalize_to_cell(
    x: float,
    y: float,
    *,
    frame_width: int,
    frame_height: int,
    resolution: int,
) -> tuple[int, int]:
    """Map normalized coordinates to heatmap cell indices."""
    cell_x = min(resolution - 1, int(x / frame_width * resolution))
    cell_y = min(resolution - 1, int(y / frame_height * resolution))
    return cell_x, cell_y


def merge_cells(cells: list[HeatmapCell]) -> list[HeatmapCell]:
    """Placeholder for cell merge logic."""
    return cells
