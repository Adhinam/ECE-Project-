"""Event envelope and types — JSONL contract between pipeline and API."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class EventType(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"
    ZONE_ENTER = "zone_enter"
    ZONE_EXIT = "zone_exit"
    ZONE_DWELL = "zone_dwell"
    DWELL = "dwell"  # legacy alias; prefer ZONE_DWELL for new events
    QUEUE_JOIN = "queue_join"
    QUEUE_LEAVE = "queue_leave"
    QUEUE_DEPTH = "queue_depth"
    POSITION_SNAPSHOT = "position_snapshot"
    REENTRY = "reentry"
    STAFF_DETECTED = "staff_detected"


class EventEnvelope(BaseModel):
    """Canonical event written to JSONL and accepted by the ingestion API."""

    schema_version: int = Field(default=1, ge=1)
    event_id: UUID
    event_type: EventType
    store_id: str
    camera_id: str
    occurred_at: datetime
    track_id: int | None = None
    global_person_id: str | None = None
    is_staff: bool = False
    confidence: float = Field(ge=0.0, le=1.0)
    calibration_method: str = "threshold_v1"
    bbox: tuple[int, int, int, int] | None = None
    frame_index: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("occurred_at", mode="before")
    @classmethod
    def parse_occurred_at(cls, v: Any) -> datetime:
        if isinstance(v, datetime):
            return v
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))

    def to_jsonl_line(self) -> str:
        return self.model_dump_json() + "\n"


def generate_event_id(
    *,
    store_id: str,
    camera_id: str,
    event_type: str,
    track_id: int,
    frame_index: int,
    subtype: str = "",
) -> UUID:
    """Deterministic event_id for idempotent replay and tests."""
    name = f"{store_id}:{camera_id}:{event_type}:{track_id}:{frame_index}:{subtype}"
    return uuid.uuid5(uuid.NAMESPACE_URL, name)
