# PROMPT:
# Create reusable event factories for pytest — deterministic IDs, journey builders,
# and staff/queue/zone variants for ingestion, funnel, and metrics tests.
#
# CHANGES MADE:
# - EventFactory with fluent builders (entry, exit, reentry, zone, queue, staff)
# - VisitorJourney helper for chronological multi-event sequences
# - Raw dict builders for ingest API partial-validation tests

"""Reusable test data factories for events and API payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

from schemas.api import EventIngestRequest
from schemas.events import EventEnvelope, EventType, generate_event_id

DEFAULT_STORE = "store-001"
DEFAULT_CAMERA = "cam-1"


class EventFactory:
    """Build valid EventEnvelope instances with deterministic or random IDs."""

    def __init__(
        self,
        *,
        store_id: str = DEFAULT_STORE,
        camera_id: str = DEFAULT_CAMERA,
        base_time: datetime | None = None,
    ) -> None:
        self.store_id = store_id
        self.camera_id = camera_id
        self._t0 = base_time or datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)
        self._frame = 0

    def _next_time(self, seconds: float = 1.0) -> datetime:
        self._frame += 1
        return self._t0 + timedelta(seconds=seconds * self._frame)

    def _eid(self, event_type: str, track_id: int, subtype: str = "") -> UUID:
        return generate_event_id(
            store_id=self.store_id,
            camera_id=self.camera_id,
            event_type=event_type,
            track_id=track_id,
            frame_index=self._frame,
            subtype=subtype,
        )

    def envelope(
        self,
        event_type: EventType | str,
        *,
        track_id: int = 1,
        frame_index: int | None = None,
        occurred_at: datetime | None = None,
        confidence: float = 0.9,
        is_staff: bool = False,
        payload: dict[str, Any] | None = None,
        event_id: UUID | None = None,
        visitor_id: str | None = None,
    ) -> EventEnvelope:
        et = event_type if isinstance(event_type, EventType) else EventType(str(event_type))
        if frame_index is not None:
            self._frame = frame_index
        pl = dict(payload or {})
        if visitor_id:
            pl.setdefault("visitor_id", visitor_id)
        eid = event_id or self._eid(et.value, track_id, subtype=pl.get("zone_id", ""))
        return EventEnvelope(
            event_id=eid,
            event_type=et,
            store_id=self.store_id,
            camera_id=self.camera_id,
            occurred_at=occurred_at or self._next_time(),
            track_id=track_id,
            global_person_id=f"track-{track_id}",
            is_staff=is_staff,
            confidence=confidence,
            payload=pl,
        )

    def entry(self, track_id: int = 1, **kwargs: Any) -> EventEnvelope:
        return self.envelope(EventType.ENTRY, track_id=track_id, payload={"line_id": "main-entrance"}, **kwargs)

    def exit(self, track_id: int = 1, **kwargs: Any) -> EventEnvelope:
        return self.envelope(EventType.EXIT, track_id=track_id, payload={"line_id": "main-entrance"}, **kwargs)

    def reentry(self, visitor_id: str, track_id: int = 1, **kwargs: Any) -> EventEnvelope:
        return self.envelope(
            EventType.REENTRY,
            track_id=track_id,
            visitor_id=visitor_id,
            payload={"visitor_id": visitor_id},
            **kwargs,
        )

    def zone_enter(self, zone_id: str, track_id: int = 1, **kwargs: Any) -> EventEnvelope:
        return self.envelope(
            EventType.ZONE_ENTER,
            track_id=track_id,
            payload={"zone_id": zone_id, "session_sequence": 1},
            **kwargs,
        )

    def zone_dwell(self, zone_id: str, dwell_seconds: float, track_id: int = 1, **kwargs: Any) -> EventEnvelope:
        return self.envelope(
            EventType.ZONE_DWELL,
            track_id=track_id,
            payload={"zone_id": zone_id, "dwell_seconds": dwell_seconds},
            **kwargs,
        )

    def queue_join(self, queue_id: str = "checkout-1", track_id: int = 1, **kwargs: Any) -> EventEnvelope:
        return self.envelope(EventType.QUEUE_JOIN, track_id=track_id, payload={"queue_id": queue_id}, **kwargs)

    def queue_depth(self, queue_id: str, depth: int, **kwargs: Any) -> EventEnvelope:
        return self.envelope(
            EventType.QUEUE_DEPTH,
            track_id=kwargs.pop("track_id", 0),
            payload={"queue_id": queue_id, "depth": depth},
            **kwargs,
        )

    def staff_entry(self, track_id: int = 99, **kwargs: Any) -> EventEnvelope:
        return self.entry(track_id=track_id, is_staff=True, **kwargs)

    def to_raw(self, envelope: EventEnvelope) -> dict[str, Any]:
        return envelope.model_dump(mode="json")

    def ingest_request(self, *envelopes: EventEnvelope, batch_id: UUID | None = None) -> EventIngestRequest:
        return EventIngestRequest(
            batch_id=batch_id or uuid4(),
            events=[self.to_raw(e) for e in envelopes],
        )


@dataclass
class VisitorJourney:
    """Chronological visitor path for funnel / metrics integration tests."""

    factory: EventFactory
    visitor_id: str = field(default_factory=lambda: str(uuid4()))
    track_id: int = 1
    events: list[EventEnvelope] = field(default_factory=list)

    def enter(self) -> VisitorJourney:
        self.events.append(self.factory.entry(self.track_id, visitor_id=self.visitor_id))
        return self

    def visit_zone(self, zone_id: str) -> VisitorJourney:
        self.events.append(self.factory.zone_enter(zone_id, self.track_id, visitor_id=self.visitor_id))
        return self

    def join_queue(self, queue_id: str = "checkout-1") -> VisitorJourney:
        self.events.append(self.factory.queue_join(queue_id, self.track_id, visitor_id=self.visitor_id))
        return self

    def checkout(self) -> VisitorJourney:
        self.events.append(self.factory.zone_enter("checkout", self.track_id, visitor_id=self.visitor_id))
        return self

    def leave(self) -> VisitorJourney:
        self.events.append(self.factory.exit(self.track_id, visitor_id=self.visitor_id))
        return self

    def reenter_after_exit(self, gap_seconds: float = 20.0) -> VisitorJourney:
        last = self.events[-1].occurred_at
        at = last + timedelta(seconds=gap_seconds)
        self.events.append(
            self.factory.reentry(self.visitor_id, self.track_id, occurred_at=at, visitor_id=self.visitor_id)
        )
        return self

    def to_session_rows(self):
        from app.metrics import SessionEventRow

        return [
            SessionEventRow(
                event_type=e.event_type.value,
                occurred_at=e.occurred_at,
                track_id=e.track_id,
                global_person_id=e.global_person_id,
                is_staff=e.is_staff,
                confidence=e.confidence,
                payload=e.payload,
            )
            for e in self.events
        ]
