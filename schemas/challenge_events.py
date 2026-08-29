"""
Purplle challenge event contract — canonical schema from the problem statement.

The API stores the internal `EventEnvelope` model; this module converts to/from the
challenge JSON shape for ingest, emit, and automated scoring harnesses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from schemas.events import EventEnvelope, EventType

# Challenge catalogue (UPPER_SNAKE in JSON)
CHALLENGE_ENTRY = "ENTRY"
CHALLENGE_EXIT = "EXIT"
CHALLENGE_ZONE_ENTER = "ZONE_ENTER"
CHALLENGE_ZONE_EXIT = "ZONE_EXIT"
CHALLENGE_ZONE_DWELL = "ZONE_DWELL"
CHALLENGE_BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
CHALLENGE_BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
CHALLENGE_REENTRY = "REENTRY"
CHALLENGE_QUEUE_DEPTH = "QUEUE_DEPTH"

_CHALLENGE_TO_INTERNAL: dict[str, EventType] = {
    CHALLENGE_ENTRY: EventType.ENTRY,
    CHALLENGE_EXIT: EventType.EXIT,
    CHALLENGE_ZONE_ENTER: EventType.ZONE_ENTER,
    CHALLENGE_ZONE_EXIT: EventType.ZONE_EXIT,
    CHALLENGE_ZONE_DWELL: EventType.ZONE_DWELL,
    CHALLENGE_BILLING_QUEUE_JOIN: EventType.QUEUE_JOIN,
    CHALLENGE_BILLING_QUEUE_ABANDON: EventType.QUEUE_LEAVE,
    CHALLENGE_REENTRY: EventType.REENTRY,
    # lowercase internal aliases accepted on ingest
    "entry": EventType.ENTRY,
    "exit": EventType.EXIT,
    "zone_enter": EventType.ZONE_ENTER,
    "zone_exit": EventType.ZONE_EXIT,
    "zone_dwell": EventType.ZONE_DWELL,
    "queue_join": EventType.QUEUE_JOIN,
    "queue_leave": EventType.QUEUE_LEAVE,
    "reentry": EventType.REENTRY,
}

_INTERNAL_TO_CHALLENGE: dict[EventType, str] = {
    EventType.ENTRY: CHALLENGE_ENTRY,
    EventType.EXIT: CHALLENGE_EXIT,
    EventType.ZONE_ENTER: CHALLENGE_ZONE_ENTER,
    EventType.ZONE_EXIT: CHALLENGE_ZONE_EXIT,
    EventType.ZONE_DWELL: CHALLENGE_ZONE_DWELL,
    EventType.DWELL: CHALLENGE_ZONE_DWELL,
    EventType.QUEUE_JOIN: CHALLENGE_BILLING_QUEUE_JOIN,
    EventType.QUEUE_LEAVE: CHALLENGE_BILLING_QUEUE_ABANDON,
    EventType.REENTRY: CHALLENGE_REENTRY,
    EventType.QUEUE_DEPTH: CHALLENGE_QUEUE_DEPTH,
}


def _misclassified_queue_depth_entry(event: EventEnvelope) -> bool:
    """True when ingest stored a QUEUE_DEPTH challenge row as internal entry."""
    return event.event_type == EventType.ENTRY and "queue_depth" in event.payload


class ChallengeEventMetadata(BaseModel):
    queue_depth: int | None = None
    sku_zone: str | None = None
    session_seq: int | None = None


class ChallengeEvent(BaseModel):
    """Required output schema from Part A of the challenge PDF."""

    event_id: UUID
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: datetime
    zone_id: str | None = None
    dwell_ms: int = 0
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: ChallengeEventMetadata = Field(default_factory=ChallengeEventMetadata)

    @field_validator("timestamp", mode="before")
    @classmethod
    def parse_timestamp(cls, v: Any) -> datetime:
        if isinstance(v, datetime):
            return v
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def format_visitor_id(visitor_uuid: UUID) -> str:
    """Challenge-style visitor id (e.g. VIS_c8a2f1)."""
    return f"VIS_{visitor_uuid.hex[:6]}"


def is_challenge_event(raw: dict[str, Any]) -> bool:
    """True when payload uses challenge field names (visitor_id + timestamp)."""
    return "visitor_id" in raw and "timestamp" in raw and "occurred_at" not in raw


def challenge_to_envelope(raw: dict[str, Any]) -> EventEnvelope:
    """Map challenge JSON object to internal EventEnvelope."""
    event = ChallengeEvent.model_validate(raw)
    internal_type = _CHALLENGE_TO_INTERNAL.get(event.event_type.upper())
    if internal_type is None:
        internal_type = _CHALLENGE_TO_INTERNAL.get(event.event_type.lower(), EventType.ENTRY)

    payload: dict[str, Any] = {
        "visitor_id": event.visitor_id,
        "zone_id": event.zone_id,
        "dwell_seconds": event.dwell_ms / 1000.0 if event.dwell_ms else 0.0,
        "session_seq": event.metadata.session_seq,
        "sku_zone": event.metadata.sku_zone,
    }
    if event.metadata.queue_depth is not None:
        payload["queue_depth"] = event.metadata.queue_depth
    if internal_type == EventType.QUEUE_LEAVE and event.event_type.upper() == CHALLENGE_BILLING_QUEUE_ABANDON:
        payload["billing_queue_abandon"] = True

    track_id = None
    if event.visitor_id.startswith("track-"):
        try:
            track_id = int(event.visitor_id.split("-", 1)[1])
        except ValueError:
            track_id = None

    return EventEnvelope(
        event_id=event.event_id,
        event_type=internal_type,
        store_id=event.store_id,
        camera_id=event.camera_id,
        occurred_at=event.timestamp,
        track_id=track_id,
        global_person_id=event.visitor_id,
        is_staff=event.is_staff,
        confidence=event.confidence,
        calibration_method="challenge_v1",
        payload={k: v for k, v in payload.items() if v is not None},
    )


def envelope_to_challenge(event: EventEnvelope) -> ChallengeEvent:
    """Serialize internal event to challenge schema for JSONL export."""
    et = event.event_type
    if _misclassified_queue_depth_entry(event):
        challenge_type = CHALLENGE_QUEUE_DEPTH
    else:
        challenge_type = _INTERNAL_TO_CHALLENGE.get(et, str(et.value).upper())

    raw_vid = event.payload.get("visitor_id") or event.global_person_id
    if raw_vid:
        visitor_id = str(raw_vid)
    elif event.track_id is not None:
        visitor_id = f"track-{event.track_id}"
    else:
        visitor_id = format_visitor_id(uuid4())

    zone_id = event.payload.get("zone_id")
    dwell_ms = int(float(event.payload.get("dwell_seconds", 0)) * 1000)
    if et in (EventType.ZONE_DWELL, EventType.DWELL) and dwell_ms == 0:
        dwell_ms = int(event.payload.get("dwell_ms", 0) or 0)

    meta = ChallengeEventMetadata(
        queue_depth=event.payload.get("queue_depth"),
        sku_zone=event.payload.get("sku_zone") or zone_id,
        session_seq=event.payload.get("session_sequence") or event.payload.get("session_seq"),
    )

    return ChallengeEvent(
        event_id=event.event_id,
        store_id=event.store_id,
        camera_id=event.camera_id,
        visitor_id=visitor_id,
        event_type=challenge_type,
        timestamp=event.occurred_at.astimezone(timezone.utc),
        zone_id=str(zone_id) if zone_id else None,
        dwell_ms=dwell_ms,
        is_staff=event.is_staff,
        confidence=event.confidence,
        metadata=meta,
    )


def parse_event_dict(raw: dict[str, Any]) -> EventEnvelope:
    """Accept either challenge or internal envelope JSON."""
    if is_challenge_event(raw):
        return challenge_to_envelope(raw)
    return EventEnvelope.model_validate(raw)
