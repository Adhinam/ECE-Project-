"""Session-based funnel analytics — stage transitions, re-entry deduplication."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.metrics import SessionEventRow
from app.pos import PosSettings, converted_visitors_from_pos, load_pos_transactions
from app.repositories import StoreMetricsRepository
from app.state import AppState
from schemas.api import FunnelResponse, FunnelStage
from schemas.events import EventType
from shared.logging import get_logger

logger = get_logger(__name__)

DEFAULT_CHECKOUT_ZONE_ID = "checkout"
DEFAULT_BILLING_QUEUE_ID = "checkout-1"
DEFAULT_REENTRY_WINDOW_SECONDS = 300.0


class FunnelStageName(StrEnum):
    ENTRY = "ENTRY"
    ZONE_VISIT = "ZONE_VISIT"
    BILLING_QUEUE = "BILLING_QUEUE"
    PURCHASE = "PURCHASE"


STAGE_ORDER: tuple[FunnelStageName, ...] = (
    FunnelStageName.ENTRY,
    FunnelStageName.ZONE_VISIT,
    FunnelStageName.BILLING_QUEUE,
    FunnelStageName.PURCHASE,
)


@dataclass
class _FunnelSession:
    """One store visit funnel (re-entries merge into the same session)."""

    session_uid: str
    visitor_key: str
    entered_at: datetime
    reached: set[FunnelStageName] = field(default_factory=set)
    exited_at: datetime | None = None
    is_active: bool = True
    reentry_merged: bool = False


@dataclass(frozen=True)
class FunnelAggregation:
    stage_counts: dict[FunnelStageName, int]
    staff_excluded: int
    reentry_merged: int
    session_count: int


def _visitor_key(row: SessionEventRow) -> str:
    vid = row.payload.get("visitor_id")
    if vid:
        return str(vid)
    if row.global_person_id:
        return row.global_person_id
    if row.track_id is not None:
        return f"track-{row.track_id}"
    return f"anon-{row.occurred_at.timestamp()}"


def _reentry_visitor_id(row: SessionEventRow) -> str | None:
    vid = row.payload.get("visitor_id") or row.payload.get("prior_visitor_id")
    return str(vid) if vid else None


def _mark_stage(session: _FunnelSession, stage: FunnelStageName) -> None:
    """Record stage only when prior funnel stages are satisfied (ordered transitions)."""
    idx = STAGE_ORDER.index(stage)
    for prior in STAGE_ORDER[:idx]:
        if prior not in session.reached:
            return
    session.reached.add(stage)


def _force_mark(session: _FunnelSession, stage: FunnelStageName) -> None:
    """Unconditionally mark a stage and its prerequisites (used for ENTRY bootstrap)."""
    idx = STAGE_ORDER.index(stage)
    for prior in STAGE_ORDER[: idx + 1]:
        session.reached.add(prior)


def aggregate_funnel_sessions(
    events: list[SessionEventRow],
    *,
    checkout_zone_id: str = DEFAULT_CHECKOUT_ZONE_ID,
    billing_queue_id: str = DEFAULT_BILLING_QUEUE_ID,
    reentry_window_seconds: float = DEFAULT_REENTRY_WINDOW_SECONDS,
) -> FunnelAggregation:
    """
    Build session funnels from chronological events.

    Re-entry deduplication: if a visitor exits and re-enters (REENTRY or ENTRY)
    within ``reentry_window_seconds``, the same funnel session is resumed — ENTRY
    is not counted again.
    """
    active: dict[str, _FunnelSession] = {}
    completed: list[_FunnelSession] = []
    recently_exited: dict[str, tuple[_FunnelSession, datetime]] = {}
    staff_excluded = 0
    reentry_merged = 0
    reentry_window = timedelta(seconds=reentry_window_seconds)

    def _close_session(session: _FunnelSession, at: datetime) -> None:
        session.is_active = False
        session.exited_at = at
        active.pop(session.visitor_key, None)
        completed.append(session)
        recently_exited[session.visitor_key] = (session, at)

    def _resume_session(prior: _FunnelSession, at: datetime) -> _FunnelSession:
        nonlocal reentry_merged
        if prior in completed:
            completed.remove(prior)
        prior.is_active = True
        prior.exited_at = None
        prior.reentry_merged = True
        reentry_merged += 1
        active[prior.visitor_key] = prior
        recently_exited.pop(prior.visitor_key, None)
        if FunnelStageName.ENTRY not in prior.reached:
            _force_mark(prior, FunnelStageName.ENTRY)
        return prior

    def _try_resume_reentry(visitor_key: str, at: datetime) -> _FunnelSession | None:
        record = recently_exited.get(visitor_key)
        if record is None:
            return None
        session, exit_at = record
        if at - exit_at > reentry_window:
            return None
        return _resume_session(session, at)

    def _start_session(visitor_key: str, at: datetime) -> _FunnelSession:
        session = _FunnelSession(
            session_uid=str(uuid4()),
            visitor_key=visitor_key,
            entered_at=at,
        )
        _force_mark(session, FunnelStageName.ENTRY)
        active[visitor_key] = session
        return session

    def _get_or_create(visitor_key: str, at: datetime) -> _FunnelSession:
        if visitor_key in active:
            return active[visitor_key]
        resumed = _try_resume_reentry(visitor_key, at)
        if resumed is not None:
            return resumed
        return _start_session(visitor_key, at)

    for row in events:
        if row.is_staff:
            staff_excluded += 1
            continue

        key = _visitor_key(row)
        et = row.event_type
        at = row.occurred_at

        if et == EventType.REENTRY.value:
            reentry_key = _reentry_visitor_id(row) or key
            if reentry_key in active:
                session = active[reentry_key]
            else:
                resumed = _try_resume_reentry(reentry_key, at)
                if resumed is not None:
                    session = resumed
                elif reentry_key in recently_exited:
                    session, exit_at = recently_exited[reentry_key]
                    if at - exit_at <= reentry_window:
                        session = _resume_session(session, at)
                    else:
                        session = _start_session(reentry_key, at)
                else:
                    session = _start_session(reentry_key, at)
            if FunnelStageName.ENTRY not in session.reached:
                _force_mark(session, FunnelStageName.ENTRY)
            continue

        if et == EventType.ENTRY.value:
            if key in active:
                continue
            resumed = _try_resume_reentry(key, at)
            if resumed is not None:
                continue
            _start_session(key, at)
            continue

        if et == EventType.EXIT.value:
            if key in active:
                _close_session(active[key], at)
            continue

        session = active.get(key)
        if session is None:
            resumed = _try_resume_reentry(key, at)
            session = resumed or active.get(key)
        if session is None:
            continue

        if et in (
            EventType.ZONE_ENTER.value,
            EventType.ZONE_EXIT.value,
            EventType.ZONE_DWELL.value,
            EventType.DWELL.value,
        ):
            zone_id = str(row.payload.get("zone_id", ""))
            if not zone_id or FunnelStageName.ENTRY not in session.reached:
                continue
            if zone_id != checkout_zone_id:
                _mark_stage(session, FunnelStageName.ZONE_VISIT)
            else:
                if FunnelStageName.ZONE_VISIT not in session.reached:
                    _mark_stage(session, FunnelStageName.ZONE_VISIT)
                if FunnelStageName.BILLING_QUEUE in session.reached:
                    _mark_stage(session, FunnelStageName.PURCHASE)

        elif et == EventType.QUEUE_JOIN.value:
            queue_id = str(row.payload.get("queue_id", ""))
            if queue_id == billing_queue_id:
                if FunnelStageName.ZONE_VISIT in session.reached:
                    _mark_stage(session, FunnelStageName.BILLING_QUEUE)

        elif et == EventType.QUEUE_LEAVE.value:
            pass

    all_sessions = completed + list(active.values())
    counts = {stage: 0 for stage in STAGE_ORDER}
    for session in all_sessions:
        for stage in STAGE_ORDER:
            if stage in session.reached:
                counts[stage] += 1

    return FunnelAggregation(
        stage_counts=counts,
        staff_excluded=staff_excluded,
        reentry_merged=reentry_merged,
        session_count=len(all_sessions),
    )


def _billing_last_seen(
    events: list[SessionEventRow],
    *,
    checkout_zone_id: str,
    billing_queue_id: str,
) -> dict[str, datetime]:
    last: dict[str, datetime] = {}
    for row in events:
        if row.is_staff:
            continue
        key = _visitor_key(row)
        if row.event_type in (
            EventType.ZONE_ENTER.value,
            EventType.ZONE_EXIT.value,
            EventType.ZONE_DWELL.value,
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


def build_funnel_stages(
    counts: dict[FunnelStageName, int],
) -> list[FunnelStage]:
    """Compute dropoff and conversion percentages per stage."""
    entry_count = counts.get(FunnelStageName.ENTRY, 0)
    stages: list[FunnelStage] = []

    for i, stage in enumerate(STAGE_ORDER):
        count = counts.get(stage, 0)
        conversion = (count / entry_count * 100.0) if entry_count else 0.0
        if i < len(STAGE_ORDER) - 1:
            next_count = counts.get(STAGE_ORDER[i + 1], 0)
            dropoff = ((count - next_count) / count * 100.0) if count else 0.0
        else:
            dropoff = 0.0
        stages.append(
            FunnelStage(
                stage=stage.value,
                count=count,
                dropoff_percent=round(dropoff, 2),
                conversion_percent=round(conversion, 2),
            )
        )
    return stages


def empty_funnel_response(
    store_id: str,
    *,
    from_time: datetime,
    to_time: datetime,
) -> FunnelResponse:
    zero_counts = {stage: 0 for stage in STAGE_ORDER}
    return FunnelResponse(
        store_id=store_id,
        from_time=from_time,
        to_time=to_time,
        stages=build_funnel_stages(zero_counts),
        conversion_rate=0.0,
        is_empty=True,
        staff_excluded_count=0,
        reentry_sessions_merged=0,
    )


class FunnelAnalyticsEngine:
    """Loads store events and computes session-based funnel analytics."""

    def __init__(self, session: AsyncSession, app_state: AppState) -> None:
        self._repo = StoreMetricsRepository(session)
        self._state = app_state

    async def get_funnel(
        self,
        store_id: str,
        *,
        from_time: datetime,
        to_time: datetime,
        checkout_zone_id: str = DEFAULT_CHECKOUT_ZONE_ID,
        billing_queue_id: str = DEFAULT_BILLING_QUEUE_ID,
        reentry_window_seconds: float = DEFAULT_REENTRY_WINDOW_SECONDS,
    ) -> FunnelResponse:
        if not self._state.db_available:
            logger.warning("funnel_degraded_no_db", store_id=store_id)
            return empty_funnel_response(store_id, from_time=from_time, to_time=to_time)

        started = time.perf_counter()
        raw_rows = await self._repo.fetch_session_events(store_id, from_time, to_time)

        if not raw_rows:
            return empty_funnel_response(store_id, from_time=from_time, to_time=to_time)

        events = [
            SessionEventRow(
                event_type=et,
                occurred_at=at,
                track_id=tid,
                global_person_id=gid,
                is_staff=staff,
                confidence=conf,
                payload=payload,
            )
            for et, at, tid, gid, staff, conf, payload in raw_rows
        ]

        aggregation = aggregate_funnel_sessions(
            events,
            checkout_zone_id=checkout_zone_id,
            billing_queue_id=billing_queue_id,
            reentry_window_seconds=reentry_window_seconds,
        )

        if aggregation.session_count == 0 or aggregation.stage_counts[FunnelStageName.ENTRY] == 0:
            return empty_funnel_response(store_id, from_time=from_time, to_time=to_time)

        pos_settings = PosSettings()
        pos_txns = load_pos_transactions(
            pos_settings.transactions_path,
            store_id=store_id,
            from_time=from_time,
            to_time=to_time,
        )
        if pos_txns:
            billing_last = _billing_last_seen(
                events,
                checkout_zone_id=checkout_zone_id,
                billing_queue_id=billing_queue_id,
            )
            converted, _pos_conf = converted_visitors_from_pos(
                transactions=pos_txns,
                billing_last_seen=billing_last,
                match_window=timedelta(minutes=pos_settings.match_window_minutes),
            )
            aggregation.stage_counts[FunnelStageName.PURCHASE] = len(converted)

        stages = build_funnel_stages(aggregation.stage_counts)
        entry_count = aggregation.stage_counts[FunnelStageName.ENTRY]
        purchase_count = aggregation.stage_counts[FunnelStageName.PURCHASE]
        conversion_rate = (purchase_count / entry_count) if entry_count else 0.0

        latency_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "funnel_computed",
            store_id=store_id,
            entry=entry_count,
            purchase=purchase_count,
            conversion_rate=round(conversion_rate, 4),
            reentry_merged=aggregation.reentry_merged,
            staff_excluded=aggregation.staff_excluded,
            latency_ms=round(latency_ms, 2),
        )

        return FunnelResponse(
            store_id=store_id,
            from_time=from_time,
            to_time=to_time,
            stages=stages,
            conversion_rate=round(conversion_rate, 4),
            is_empty=False,
            staff_excluded_count=aggregation.staff_excluded,
            reentry_sessions_merged=aggregation.reentry_merged,
        )
