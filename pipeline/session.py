"""Visitor session engine — UUID identities, zones, dwell, and lifecycle tracking."""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Iterator
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from schemas.events import EventEnvelope, EventType
from shared.logging import get_logger

logger = get_logger(__name__)


class SessionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"


class ZoneVisitSnapshot(BaseModel):
    """One zone visit within a store visitor session."""

    zone_id: str
    zone_session_sequence: int
    entered_at: datetime
    exited_at: datetime | None = None
    dwell_seconds: float = 0.0
    dwell_event_count: int = 0

    @field_validator("entered_at", "exited_at", mode="before")
    @classmethod
    def parse_dt(cls, v: Any) -> Any:
        if v is None or isinstance(v, datetime):
            return v
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


class VisitorSession(BaseModel):
    """
    A single store visit by one person (may span multiple track IDs until Re-ID).

    `visitor_id` is a UUID assigned at store ENTRY; `session_sequence` is monotonic
    per store for the processing run.
    """

    visitor_id: UUID
    store_id: str
    session_sequence: int
    status: SessionStatus = SessionStatus.ACTIVE
    track_id: int | None = None
    camera_id: str | None = None
    entry_at: datetime
    exit_at: datetime | None = None
    entry_line_id: str | None = None
    exit_line_id: str | None = None
    last_seen_at: datetime
    visited_zones: list[ZoneVisitSnapshot] = Field(default_factory=list)
    total_dwell_seconds: float = 0.0
    is_staff: bool = False
    reentry_pending: bool = False  # hook for future re-entry detector

    @field_validator("visitor_id", mode="before")
    @classmethod
    def parse_uuid(cls, v: Any) -> UUID:
        if isinstance(v, UUID):
            return v
        return UUID(str(v))

    @field_validator("entry_at", "exit_at", "last_seen_at", mode="before")
    @classmethod
    def parse_dt(cls, v: Any) -> Any:
        if v is None or isinstance(v, datetime):
            return v
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))

    @property
    def duration_seconds(self) -> float:
        end = self.exit_at or self.last_seen_at
        return max(0.0, (end - self.entry_at).total_seconds())

    def active_zone_visit(self, zone_id: str) -> ZoneVisitSnapshot | None:
        for z in reversed(self.visited_zones):
            if z.zone_id == zone_id and z.exited_at is None:
                return z
        return None

    def zone_ids_visited(self) -> list[str]:
        return list(dict.fromkeys(z.zone_id for z in self.visited_zones))


class VisitorSummary(BaseModel):
    """Aggregated view of a completed (or active) visitor session."""

    visitor_id: UUID
    store_id: str
    session_sequence: int
    status: SessionStatus
    entry_at: datetime
    exit_at: datetime | None
    duration_seconds: float
    zones_visited: list[str]
    total_dwell_seconds: float
    track_id: int | None


class SessionSettings(BaseSettings):
    """Configurable session lifecycle behaviour."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
        env_prefix="SESSION_",
    )

    timeout_seconds: float = Field(
        default=300.0,
        ge=1.0,
        validation_alias="TIMEOUT_SECONDS",
        description="End active session if no events received within this window",
    )
    max_completed: int = Field(
        default=10_000,
        ge=100,
        validation_alias="MAX_COMPLETED",
        description="Ring buffer size for completed sessions (memory bound)",
    )
    max_track_mappings: int = Field(
        default=5_000,
        ge=100,
        validation_alias="MAX_TRACK_MAPPINGS",
        description="Max concurrent track_id → visitor_id mappings",
    )

    @property
    def session_timeout_seconds(self) -> float:
        return self.timeout_seconds

    @property
    def max_completed_sessions(self) -> int:
        return self.max_completed


@dataclass
class ReentryContext:
    """
    Placeholder state for future re-entry detection.

    When a visitor exits and re-enters within the reentry window, the engine may
    resume the same visitor_id instead of starting a new session_sequence.
    """

    prior_visitor_id: UUID | None = None
    last_exit_at: datetime | None = None
    reentry_window_seconds: float = 300.0

    def should_resume(self, now: datetime) -> bool:
        if self.prior_visitor_id is None or self.last_exit_at is None:
            return False
        return (now - self.last_exit_at).total_seconds() <= self.reentry_window_seconds


class SessionEngine:
    """
    Maintains active and completed visitor sessions driven by pipeline events.

    Feed events from entry/exit and zone engines via `process_event` or `process_events`.
    """

    def __init__(
        self,
        store_id: str,
        settings: SessionSettings | None = None,
        *,
        reentry_context: ReentryContext | None = None,
    ) -> None:
        self._store_id = store_id
        self._settings = settings or SessionSettings()
        self._reentry = reentry_context or ReentryContext()

        self._active: dict[UUID, VisitorSession] = {}
        self._completed: deque[VisitorSession] = deque(maxlen=self._settings.max_completed_sessions)
        self._track_to_visitor: dict[int, UUID] = {}
        self._next_session_sequence: int = 0

    @property
    def settings(self) -> SessionSettings:
        return self._settings

    @property
    def active_sessions(self) -> list[VisitorSession]:
        return list(self._active.values())

    @property
    def completed_sessions(self) -> list[VisitorSession]:
        return list(self._completed)

    def visitor_summaries(self, *, include_active: bool = True) -> list[VisitorSummary]:
        """Build summary DTOs for dashboards and API export."""
        sessions: list[VisitorSession] = list(self._completed)
        if include_active:
            sessions.extend(self._active.values())
        return [self._to_summary(s) for s in sessions]

    def get_session(self, visitor_id: UUID) -> VisitorSession | None:
        if visitor_id in self._active:
            return self._active[visitor_id]
        for s in reversed(self._completed):
            if s.visitor_id == visitor_id:
                return s
        return None

    def get_active_by_track(self, track_id: int) -> VisitorSession | None:
        vid = self._track_to_visitor.get(track_id)
        if vid is None:
            return None
        return self._active.get(vid)

    def process_events(self, events: list[EventEnvelope]) -> list[VisitorSession]:
        """Apply a batch of events; return sessions that changed state."""
        changed: list[VisitorSession] = []
        for event in events:
            session = self.process_event(event)
            if session is not None:
                changed.append(session)
        return changed

    def process_event(self, event: EventEnvelope) -> VisitorSession | None:
        """Update session state from a single pipeline event."""
        self._cleanup_stale(event.occurred_at)

        if event.event_type == EventType.ENTRY:
            return self._handle_entry(event)
        if event.event_type == EventType.REENTRY:
            return self._handle_reentry(event)
        if event.event_type == EventType.EXIT:
            return self._handle_exit(event)
        if event.event_type == EventType.ZONE_ENTER:
            return self._handle_zone_enter(event)
        if event.event_type == EventType.ZONE_EXIT:
            return self._handle_zone_exit(event)
        if event.event_type in (EventType.ZONE_DWELL, EventType.DWELL):
            return self._handle_zone_dwell(event)
        return None

    def close_all_active(
        self,
        *,
        at: datetime | None = None,
        reason: SessionStatus = SessionStatus.COMPLETED,
    ) -> list[VisitorSession]:
        """Force-close all active sessions (e.g. end of video)."""
        now = at or datetime.now(timezone.utc)
        closed: list[VisitorSession] = []
        for vid in list(self._active.keys()):
            session = self._complete_session(self._active[vid], now, reason)
            closed.append(session)
        return closed

    def _next_sequence(self) -> int:
        self._next_session_sequence += 1
        return self._next_session_sequence

    def _assign_visitor_id(self, event: EventEnvelope) -> UUID:
        """Create a new UUID visitor identity."""
        return uuid4()

    def resume_visitor(self, visitor_id: UUID, event: EventEnvelope) -> VisitorSession:
        """
        Resume a store visit after REENTRY (same visitor_id and session_sequence).

        Called when lightweight Re-ID matches a returning visitor.
        """
        prior = self.get_session(visitor_id)
        seq = prior.session_sequence if prior else self._next_sequence()

        session = VisitorSession(
            visitor_id=visitor_id,
            store_id=self._store_id,
            session_sequence=seq,
            status=SessionStatus.ACTIVE,
            track_id=event.track_id,
            camera_id=event.camera_id,
            entry_at=event.occurred_at,
            last_seen_at=event.occurred_at,
            entry_line_id=(
                str(line)
                if (line := event.payload.get("line_id") or event.payload.get("entry_line_id"))
                else None
            ),
            is_staff=event.is_staff,
            visited_zones=[],
            total_dwell_seconds=prior.total_dwell_seconds if prior else 0.0,
        )
        self._active[visitor_id] = session
        if event.track_id is not None:
            self._track_to_visitor[event.track_id] = visitor_id

        self._reentry.prior_visitor_id = visitor_id
        self._reentry.last_exit_at = None

        logger.info(
            "visitor_session_resumed",
            visitor_id=str(visitor_id),
            session_sequence=seq,
            track_id=event.track_id,
        )
        return session

    def _handle_reentry(self, event: EventEnvelope) -> VisitorSession:
        vid_raw = event.payload.get("visitor_id") or event.global_person_id
        visitor_id = UUID(str(vid_raw))
        return self.resume_visitor(visitor_id, event)

    def _handle_entry(self, event: EventEnvelope) -> VisitorSession:
        visitor_id = self._assign_visitor_id(event)
        seq = self._next_sequence()
        line_id = event.payload.get("line_id")

        session = VisitorSession(
            visitor_id=visitor_id,
            store_id=self._store_id,
            session_sequence=seq,
            status=SessionStatus.ACTIVE,
            track_id=event.track_id,
            camera_id=event.camera_id,
            entry_at=event.occurred_at,
            last_seen_at=event.occurred_at,
            entry_line_id=str(line_id) if line_id else None,
            is_staff=event.is_staff,
        )
        self._active[visitor_id] = session
        if event.track_id is not None:
            self._track_to_visitor[event.track_id] = visitor_id
            self._trim_track_mappings()

        logger.info(
            "visitor_session_started",
            visitor_id=str(visitor_id),
            session_sequence=seq,
            track_id=event.track_id,
            entry_line=line_id,
        )
        return session

    def _handle_exit(self, event: EventEnvelope) -> VisitorSession | None:
        session = self._resolve_session(event)
        if session is None:
            logger.warning("exit_without_session", track_id=event.track_id)
            return None

        session.exit_line_id = str(event.payload.get("line_id", "")) or None
        session.last_seen_at = event.occurred_at
        self._close_open_zone_visits(session, event.occurred_at)
        completed = self._complete_session(session, event.occurred_at, SessionStatus.COMPLETED)

        self._reentry.prior_visitor_id = completed.visitor_id
        self._reentry.last_exit_at = event.occurred_at

        logger.info(
            "visitor_session_ended",
            visitor_id=str(completed.visitor_id),
            session_sequence=completed.session_sequence,
            duration_seconds=round(completed.duration_seconds, 2),
        )
        return completed

    def _handle_zone_enter(self, event: EventEnvelope) -> VisitorSession | None:
        session = self._resolve_session(event)
        if session is None:
            return None

        zone_id = str(event.payload.get("zone_id", ""))
        if not zone_id:
            return session

        if session.active_zone_visit(zone_id) is not None:
            return session

        zone_seq = int(event.payload.get("session_sequence", len(session.visited_zones) + 1))
        session.visited_zones.append(
            ZoneVisitSnapshot(
                zone_id=zone_id,
                zone_session_sequence=zone_seq,
                entered_at=event.occurred_at,
            )
        )
        session.last_seen_at = event.occurred_at
        if event.track_id is not None:
            self._track_to_visitor[event.track_id] = session.visitor_id

        logger.debug(
            "visitor_zone_enter",
            visitor_id=str(session.visitor_id),
            zone_id=zone_id,
        )
        return session

    def _handle_zone_exit(self, event: EventEnvelope) -> VisitorSession | None:
        session = self._resolve_session(event)
        if session is None:
            return None

        zone_id = str(event.payload.get("zone_id", ""))
        visit = session.active_zone_visit(zone_id)
        if visit is None:
            return session

        visit.exited_at = event.occurred_at
        dwell = float(event.payload.get("dwell_seconds", 0.0))
        visit.dwell_seconds = max(visit.dwell_seconds, dwell)
        session.total_dwell_seconds = sum(z.dwell_seconds for z in session.visited_zones)
        session.last_seen_at = event.occurred_at

        logger.debug(
            "visitor_zone_exit",
            visitor_id=str(session.visitor_id),
            zone_id=zone_id,
            dwell_seconds=visit.dwell_seconds,
        )
        return session

    def _handle_zone_dwell(self, event: EventEnvelope) -> VisitorSession | None:
        session = self._resolve_session(event)
        if session is None:
            return None

        zone_id = str(event.payload.get("zone_id", ""))
        visit = session.active_zone_visit(zone_id)
        if visit is None:
            zone_seq = int(event.payload.get("session_sequence", 1))
            visit = ZoneVisitSnapshot(
                zone_id=zone_id,
                zone_session_sequence=zone_seq,
                entered_at=event.occurred_at,
            )
            session.visited_zones.append(visit)

        dwell = float(event.payload.get("dwell_seconds", 0.0))
        visit.dwell_seconds = max(visit.dwell_seconds, dwell)
        visit.dwell_event_count += 1
        session.total_dwell_seconds = sum(z.dwell_seconds for z in session.visited_zones)
        session.last_seen_at = event.occurred_at
        return session

    def _resolve_session(self, event: EventEnvelope) -> VisitorSession | None:
        """Find active session by global_person_id, track_id, or mapping."""
        if event.global_person_id and event.global_person_id.startswith("track-"):
            try:
                tid = int(event.global_person_id.split("-", 1)[1])
                vid = self._track_to_visitor.get(tid)
                if vid and vid in self._active:
                    return self._active[vid]
            except (ValueError, IndexError):
                pass

        if event.track_id is not None:
            vid = self._track_to_visitor.get(event.track_id)
            if vid and vid in self._active:
                return self._active[vid]

        return None

    def _close_open_zone_visits(self, session: VisitorSession, at: datetime) -> None:
        for visit in session.visited_zones:
            if visit.exited_at is None:
                visit.exited_at = at
                if visit.dwell_seconds <= 0:
                    visit.dwell_seconds = max(
                        0.0, (at - visit.entered_at).total_seconds()
                    )
        session.total_dwell_seconds = sum(z.dwell_seconds for z in session.visited_zones)

    def _complete_session(
        self,
        session: VisitorSession,
        at: datetime,
        status: SessionStatus,
    ) -> VisitorSession:
        session.status = status
        session.exit_at = session.exit_at or at
        session.last_seen_at = at
        self._close_open_zone_visits(session, at)

        self._active.pop(session.visitor_id, None)
        if session.track_id is not None:
            self._track_to_visitor.pop(session.track_id, None)

        self._completed.append(session)
        return session

    def _cleanup_stale(self, now: datetime) -> None:
        """End sessions that exceeded inactivity timeout (memory-safe)."""
        timeout = timedelta(seconds=self._settings.session_timeout_seconds)
        stale_ids: list[UUID] = []
        for vid, session in self._active.items():
            if now - session.last_seen_at > timeout:
                stale_ids.append(vid)

        for vid in stale_ids:
            session = self._active[vid]
            logger.warning(
                "visitor_session_timeout",
                visitor_id=str(vid),
                session_sequence=session.session_sequence,
                idle_seconds=(now - session.last_seen_at).total_seconds(),
            )
            self._complete_session(session, now, SessionStatus.TIMED_OUT)

    def _trim_track_mappings(self) -> None:
        if len(self._track_to_visitor) <= self._settings.max_track_mappings:
            return
        # Drop mappings that no longer point to active sessions
        for tid in list(self._track_to_visitor.keys()):
            vid = self._track_to_visitor[tid]
            if vid not in self._active:
                del self._track_to_visitor[tid]
        if len(self._track_to_visitor) > self._settings.max_track_mappings:
            excess = len(self._track_to_visitor) - self._settings.max_track_mappings
            for tid in list(self._track_to_visitor.keys())[:excess]:
                del self._track_to_visitor[tid]

    def _to_summary(self, session: VisitorSession) -> VisitorSummary:
        return VisitorSummary(
            visitor_id=session.visitor_id,
            store_id=session.store_id,
            session_sequence=session.session_sequence,
            status=session.status,
            entry_at=session.entry_at,
            exit_at=session.exit_at,
            duration_seconds=session.duration_seconds,
            zones_visited=session.zone_ids_visited(),
            total_dwell_seconds=session.total_dwell_seconds,
            track_id=session.track_id,
        )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def session_to_dict(session: VisitorSession) -> dict[str, Any]:
    """Serialize a session to a JSON-compatible dict."""
    return session.model_dump(mode="json")


def session_from_dict(data: dict[str, Any]) -> VisitorSession:
    """Deserialize a session from dict (e.g. JSON load)."""
    return VisitorSession.model_validate(data)


def session_to_json(session: VisitorSession, *, indent: int | None = None) -> str:
    return json.dumps(session_to_dict(session), indent=indent, default=str)


def sessions_to_jsonl(sessions: Iterator[VisitorSession] | list[VisitorSession]) -> str:
    """One JSON object per line — suitable for append-only session logs."""
    lines = [json.dumps(session_to_dict(s), default=str) for s in sessions]
    return "\n".join(lines) + ("\n" if lines else "")


def sessions_from_jsonl(text: str) -> list[VisitorSession]:
    """Load sessions from JSONL text."""
    out: list[VisitorSession] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(session_from_dict(json.loads(line)))
    return out


def summaries_to_json(summaries: list[VisitorSummary], *, indent: int | None = None) -> str:
    payload = [s.model_dump(mode="json") for s in summaries]
    return json.dumps(payload, indent=indent, default=str)


def write_sessions_jsonl(path: str | Path, sessions: list[VisitorSession]) -> None:
    """Persist completed/active sessions to a JSONL file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        fh.write(sessions_to_jsonl(sessions))
