"""Store metrics engine — session-aware, real-time calculations from raw events."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.pos import PosSettings, converted_visitors_from_pos, load_pos_transactions
from app.repositories import StoreMetricsRepository
from app.state import AppState
from schemas.api import (
    MetricConfidence,
    QueueDepthMetric,
    StoreMetricsConfidence,
    StoreMetricsResponse,
    ZoneDwellMetric,
)
from schemas.events import EventType
from shared.logging import get_logger

logger = get_logger(__name__)

DEFAULT_CHECKOUT_ZONE_ID = "checkout"
MIN_SAMPLES_HIGH = 10
MIN_SAMPLES_MEDIUM = 3


@dataclass(frozen=True)
class SessionEventRow:
    event_type: str
    occurred_at: datetime
    track_id: int | None
    global_person_id: str | None
    is_staff: bool
    confidence: float
    payload: dict[str, Any]


@dataclass
class _VisitorSession:
    visitor_key: str
    entered_at: datetime
    exited: bool = False
    checkout_visited: bool = False
    queue_joins: set[str] = field(default_factory=set)
    queue_leaves: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class SessionMetrics:
    unique_visitors: int
    conversion_rate: float
    abandonment_rate: float
    visitors_inside: int
    has_purchases: bool
    staff_excluded: int
    confidence_visitors: MetricConfidence
    confidence_conversion: MetricConfidence
    confidence_abandonment: MetricConfidence


def _collect_visitor_sessions(
    events: list[SessionEventRow],
) -> tuple[set[str], dict[str, datetime]]:
    visitor_ids: set[str] = set()
    entries: dict[str, datetime] = {}
    for row in events:
        if row.is_staff:
            continue
        key = _visitor_key(row)
        visitor_ids.add(key)
        if row.event_type == EventType.ENTRY.value:
            entries[key] = row.occurred_at
    return visitor_ids, entries


def _visitor_key(row: SessionEventRow) -> str:
    vid = row.payload.get("visitor_id")
    if vid:
        return str(vid)
    if row.global_person_id:
        return row.global_person_id
    if row.track_id is not None:
        return f"track-{row.track_id}"
    return f"anon-{row.occurred_at.timestamp()}"


def _billing_last_seen(
    events: list[SessionEventRow],
    *,
    checkout_zone_id: str,
    billing_queue_id: str,
) -> dict[str, datetime]:
    """
    Last observed timestamp per visitor in billing context.

    Billing context is defined as:
    - checkout zone activity (ZONE_ENTER/DWELL/EXIT) OR
    - queue join for the configured billing queue id
    """
    last: dict[str, datetime] = {}
    for row in events:
        if row.is_staff:
            continue
        key = _visitor_key(row)
        if row.event_type in (
            EventType.ZONE_ENTER.value,
            EventType.ZONE_DWELL.value,
            EventType.ZONE_EXIT.value,
            EventType.DWELL.value,
        ):
            zone_id = str(row.payload.get("zone_id", ""))
            if zone_id == checkout_zone_id:
                last[key] = row.occurred_at
        elif row.event_type == EventType.QUEUE_JOIN.value:
            queue_id = str(row.payload.get("queue_id", ""))
            if queue_id == billing_queue_id:
                last[key] = row.occurred_at
    return last


def _confidence_from_count(count: int) -> MetricConfidence:
    if count >= MIN_SAMPLES_HIGH:
        return MetricConfidence.HIGH
    if count >= MIN_SAMPLES_MEDIUM:
        return MetricConfidence.MEDIUM
    if count > 0:
        return MetricConfidence.LOW
    return MetricConfidence.UNAVAILABLE


def compute_session_metrics(
    events: list[SessionEventRow],
    *,
    checkout_zone_id: str,
) -> SessionMetrics:
    """
    Session-aware metrics from chronological visitor events (staff excluded upstream).

    - Unique visitors: distinct visitor keys with at least one ENTRY.
    - Conversion: sessions visiting checkout zone / sessions with ENTRY.
    - Abandonment: sessions with ENTRY and EXIT but no checkout / sessions with ENTRY.
    - Zero-purchase stores: conversion_rate is 0.0 (not null/NaN).
    """
    sessions: dict[str, _VisitorSession] = {}
    staff_excluded = 0

    for row in events:
        if row.is_staff:
            staff_excluded += 1
            continue

        key = _visitor_key(row)
        et = row.event_type

        if et == EventType.ENTRY.value:
            sessions[key] = _VisitorSession(visitor_key=key, entered_at=row.occurred_at)
        elif et == EventType.REENTRY.value:
            if key not in sessions:
                sessions[key] = _VisitorSession(visitor_key=key, entered_at=row.occurred_at)
        elif et == EventType.EXIT.value:
            if key in sessions:
                sessions[key].exited = True
        elif et in (EventType.ZONE_ENTER.value, EventType.ZONE_DWELL.value, EventType.ZONE_EXIT.value):
            zone_id = str(row.payload.get("zone_id", ""))
            if zone_id == checkout_zone_id and key in sessions:
                sessions[key].checkout_visited = True
        elif et == EventType.QUEUE_JOIN.value:
            queue_id = str(row.payload.get("queue_id", ""))
            if key in sessions and queue_id:
                sessions[key].queue_joins.add(queue_id)
        elif et == EventType.QUEUE_LEAVE.value:
            queue_id = str(row.payload.get("queue_id", ""))
            if key in sessions and queue_id:
                sessions[key].queue_leaves.add(queue_id)

    entered_sessions = [s for s in sessions.values() if s.entered_at]
    unique = len(entered_sessions)

    if unique == 0:
        return SessionMetrics(
            unique_visitors=0,
            conversion_rate=0.0,
            abandonment_rate=0.0,
            visitors_inside=0,
            has_purchases=False,
            staff_excluded=staff_excluded,
            confidence_visitors=MetricConfidence.UNAVAILABLE,
            confidence_conversion=MetricConfidence.UNAVAILABLE,
            confidence_abandonment=MetricConfidence.UNAVAILABLE,
        )

    converted = sum(1 for s in entered_sessions if s.checkout_visited)
    conversion_rate = converted / unique

    abandoned = sum(
        1 for s in entered_sessions if s.exited and not s.checkout_visited
    )
    abandonment_rate = abandoned / unique

    inside = sum(1 for s in entered_sessions if not s.exited)

    return SessionMetrics(
        unique_visitors=unique,
        conversion_rate=conversion_rate,
        abandonment_rate=abandonment_rate,
        visitors_inside=inside,
        has_purchases=converted > 0,
        staff_excluded=staff_excluded,
        confidence_visitors=_confidence_from_count(unique),
        confidence_conversion=_confidence_from_count(unique),
        confidence_abandonment=_confidence_from_count(unique),
    )


def build_zone_metrics(
    rows: list[tuple[str, float, int, float]],
) -> tuple[list[ZoneDwellMetric], MetricConfidence]:
    if not rows:
        return [], MetricConfidence.UNAVAILABLE

    zones = [
        ZoneDwellMetric(
            zone_id=zone_id,
            avg_dwell_seconds=round(avg_dwell, 2),
            sample_count=sample_count,
            confidence=_confidence_from_count(sample_count),
        )
        for zone_id, avg_dwell, sample_count, _avg_conf in rows
    ]
    total_samples = sum(z.sample_count for z in zones)
    return zones, _confidence_from_count(total_samples)


def build_queue_metrics(
    rows: list[tuple[str, int, datetime, float]],
) -> tuple[list[QueueDepthMetric], MetricConfidence]:
    if not rows:
        return [], MetricConfidence.UNAVAILABLE

    queues = [
        QueueDepthMetric(
            queue_id=queue_id,
            depth=depth,
            as_of=as_of,
            confidence=_confidence_from_count(1),
        )
        for queue_id, depth, as_of, _conf in rows
    ]
    return queues, _confidence_from_count(len(queues))


def empty_store_response(
    store_id: str,
    *,
    as_of: datetime,
    window_minutes: int,
) -> StoreMetricsResponse:
    """Safe defaults when the store has no activity in the window."""
    unavailable = StoreMetricsConfidence(
        unique_visitors=MetricConfidence.UNAVAILABLE,
        conversion_rate=MetricConfidence.UNAVAILABLE,
        abandonment_rate=MetricConfidence.UNAVAILABLE,
        zone_dwell=MetricConfidence.UNAVAILABLE,
        queue_depth=MetricConfidence.UNAVAILABLE,
    )
    return StoreMetricsResponse(
        store_id=store_id,
        as_of=as_of,
        window_minutes=window_minutes,
        unique_visitors=0,
        conversion_rate=0.0,
        abandonment_rate=0.0,
        zones=[],
        queues=[],
        visitors_inside=0,
        staff_excluded_count=0,
        is_empty=True,
        has_purchases=False,
        confidence=unavailable,
    )


class StoreMetricsEngine:
    """Computes real-time store KPIs with staff exclusion and session awareness."""

    def __init__(self, session: AsyncSession, app_state: AppState) -> None:
        self._repo = StoreMetricsRepository(session)
        self._state = app_state

    async def get_store_metrics(
        self,
        store_id: str,
        *,
        window_minutes: int = 15,
        checkout_zone_id: str = DEFAULT_CHECKOUT_ZONE_ID,
    ) -> StoreMetricsResponse:
        now = datetime.now(timezone.utc)
        from_time = now - timedelta(minutes=window_minutes)

        if not self._state.db_available:
            logger.warning("store_metrics_degraded", store_id=store_id)
            response = empty_store_response(store_id, as_of=now, window_minutes=window_minutes)
            response.confidence = StoreMetricsConfidence()
            return response

        started = time.perf_counter()

        has_activity, raw_session_rows, zone_rows, queue_rows, staff_count = await self._repo.fetch_all(
            store_id,
            from_time,
            now,
        )

        if not has_activity:
            latency_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "store_metrics_empty",
                store_id=store_id,
                window_minutes=window_minutes,
                latency_ms=round(latency_ms, 2),
            )
            return empty_store_response(store_id, as_of=now, window_minutes=window_minutes)

        session_rows = [
            SessionEventRow(
                event_type=et,
                occurred_at=at,
                track_id=tid,
                global_person_id=gid,
                is_staff=staff,
                confidence=conf,
                payload=payload,
            )
            for et, at, tid, gid, staff, conf, payload in raw_session_rows
        ]
        session_metrics = compute_session_metrics(session_rows, checkout_zone_id=checkout_zone_id)
        zones, zone_conf = build_zone_metrics(zone_rows)
        queues, queue_conf = build_queue_metrics(queue_rows)

        conversion_rate = session_metrics.conversion_rate
        has_purchases = session_metrics.has_purchases
        pos_settings = PosSettings()
        pos_txns = load_pos_transactions(
            pos_settings.transactions_path,
            store_id=store_id,
            from_time=from_time,
            to_time=now,
        )
        pos_conversion_rate: float | None = None
        pos_transaction_count = 0
        if pos_txns:
            visitor_ids, _entry_times = _collect_visitor_sessions(session_rows)
            billing_last = _billing_last_seen(
                session_rows,
                checkout_zone_id=checkout_zone_id,
                billing_queue_id="checkout-1",
            )
            converted, _pos_conf = converted_visitors_from_pos(
                transactions=pos_txns,
                billing_last_seen=billing_last,
                match_window=timedelta(minutes=pos_settings.match_window_minutes),
            )
            pos_transaction_count = len(pos_txns)
            pos_rate = (len(converted) / len(visitor_ids)) if visitor_ids else 0.0
            pos_conversion_rate = round(pos_rate, 4)
            # If POS is present, treat it as authoritative for conversion.
            conversion_rate = round(pos_rate, 4)
            has_purchases = len(converted) > 0

        response = StoreMetricsResponse(
            store_id=store_id,
            as_of=now,
            window_minutes=window_minutes,
            unique_visitors=session_metrics.unique_visitors,
            conversion_rate=round(conversion_rate, 4),
            abandonment_rate=round(session_metrics.abandonment_rate, 4),
            zones=zones,
            queues=queues,
            visitors_inside=session_metrics.visitors_inside,
            staff_excluded_count=max(session_metrics.staff_excluded, staff_count),
            is_empty=False,
            has_purchases=has_purchases,
            pos_conversion_rate=pos_conversion_rate,
            pos_transaction_count=pos_transaction_count,
            confidence=StoreMetricsConfidence(
                unique_visitors=session_metrics.confidence_visitors,
                conversion_rate=session_metrics.confidence_conversion,
                abandonment_rate=session_metrics.confidence_abandonment,
                zone_dwell=zone_conf,
                queue_depth=queue_conf,
            ),
        )

        latency_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "store_metrics_computed",
            store_id=store_id,
            window_minutes=window_minutes,
            unique_visitors=response.unique_visitors,
            conversion_rate=response.conversion_rate,
            abandonment_rate=response.abandonment_rate,
            zone_count=len(zones),
            queue_count=len(queues),
            staff_excluded=response.staff_excluded_count,
            is_empty=response.is_empty,
            has_purchases=response.has_purchases,
            latency_ms=round(latency_ms, 2),
        )
        return response
