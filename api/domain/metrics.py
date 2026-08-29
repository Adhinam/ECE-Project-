"""Real-time metrics domain — rollup calculations."""

from __future__ import annotations

from schemas.api import RealtimeMetricsResponse


def compute_visitors_inside(entries: int, exits: int) -> int:
    return max(0, entries - exits)


def build_realtime_metrics(
    *,
    store_id: str,
    as_of,
    entries: int,
    exits: int,
    conversion_rate: float,
    avg_queue_depth: float | None,
    max_queue_depth: float | None,
    staff_excluded: int,
) -> RealtimeMetricsResponse:
    return RealtimeMetricsResponse(
        store_id=store_id,
        as_of=as_of,
        visitors_inside=compute_visitors_inside(entries, exits),
        entries=entries,
        exits=exits,
        conversion_rate=conversion_rate,
        avg_queue_depth=avg_queue_depth,
        max_queue_depth=max_queue_depth,
        staff_excluded_count=staff_excluded,
    )
