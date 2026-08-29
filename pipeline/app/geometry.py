"""Geometry utilities — point-in-polygon, line crossing."""

from __future__ import annotations


def point_in_polygon(x: float, y: float, polygon: list[list[int]]) -> bool:
    """Ray-casting algorithm — placeholder implementation."""
    _ = (x, y, polygon)
    return False


def line_crossed(
    prev: tuple[float, float],
    curr: tuple[float, float],
    p1: list[int],
    p2: list[int],
) -> str | None:
    """Return 'in' or 'out' if segment crosses line, else None."""
    _ = (prev, curr, p1, p2)
    return None
