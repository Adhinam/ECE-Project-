"""Pipeline event schemas — validation, serialization, and emission helpers."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Iterator
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError, field_validator

# Canonical contracts live in top-level `schemas` package
from schemas.events import EventEnvelope, EventType, generate_event_id

__all__ = [
    "EventEnvelope",
    "EventType",
    "EventMetadata",
    "EventBuildParams",
    "ValidationResult",
    "ValidationIssue",
    "DedupKey",
    "generate_event_id",
    "generate_unique_event_id",
    "utc_now",
    "timestamp_for_frame",
    "validate_event",
    "validate_batch",
    "parse_jsonl_line",
    "event_to_dict",
    "event_from_dict",
    "event_to_jsonl_line",
    "events_to_jsonl",
    "events_from_jsonl",
    "iter_jsonl_events",
    "build_event",
    "merge_metadata",
    "apply_confidence_floor",
    "dedup_key",
    "is_duplicate",
]


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    field: str
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR


class ValidationResult(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)

    @classmethod
    def ok(cls) -> ValidationResult:
        return cls(valid=True)

    @classmethod
    def fail(cls, *issues: ValidationIssue) -> ValidationResult:
        return cls(valid=False, issues=list(issues))


class EventMetadata(BaseModel):
    """
    Optional emission metadata merged into event.payload under `_meta`.

    Keeps top-level EventEnvelope aligned with API contract while attaching
    pipeline context (source module, run id, video file, etc.).
    """

    source: str | None = None
    pipeline_run_id: str | None = None
    video_path: str | None = None
    processor_version: str = "0.1.0"
    extra: dict[str, Any] = Field(default_factory=dict)

    def to_payload_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "source": self.source,
            "pipeline_run_id": self.pipeline_run_id,
            "video_path": self.video_path,
            "processor_version": self.processor_version,
        }
        data.update(self.extra)
        return {k: v for k, v in data.items() if v is not None}


class EventBuildParams(BaseModel):
    """Typed inputs for constructing a schema-compliant EventEnvelope."""

    event_type: EventType
    store_id: str
    camera_id: str
    occurred_at: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    track_id: int | None = None
    global_person_id: str | None = None
    is_staff: bool = False
    calibration_method: str = "threshold_v1"
    bbox: tuple[int, int, int, int] | None = None
    frame_index: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: EventMetadata | None = None
    schema_version: int = Field(default=1, ge=1)
    # Idempotency: deterministic UUID from generate_event_id when set
    event_id: UUID | None = None
    idempotency_subtype: str = ""

    @field_validator("occurred_at", mode="before")
    @classmethod
    def parse_occurred_at(cls, v: Any) -> datetime:
        if isinstance(v, datetime):
            return v
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def utc_now() -> datetime:
    """Timezone-aware UTC timestamp for events."""
    return datetime.now(timezone.utc)


def timestamp_for_frame(
    frame_index: int,
    *,
    fps: float,
    started_at: datetime | None = None,
) -> datetime:
    """Map a video frame index to an absolute occurred_at timestamp."""
    base = started_at or utc_now()
    if fps <= 0:
        return base
    return base + timedelta(seconds=frame_index / fps)


def generate_unique_event_id() -> UUID:
    """Random UUID v4 — use when deterministic ids are not required."""
    return uuid4()


def apply_confidence_floor(confidence: float, floor: float = 0.0) -> float:
    return min(1.0, max(floor, confidence))


def merge_metadata(payload: dict[str, Any], metadata: EventMetadata | None) -> dict[str, Any]:
    merged = dict(payload)
    if metadata is not None:
        existing = merged.get("_meta", {})
        if not isinstance(existing, dict):
            existing = {}
        existing.update(metadata.to_payload_dict())
        merged["_meta"] = existing
    return merged


def build_event(params: EventBuildParams) -> EventEnvelope:
    """
    Build a validated EventEnvelope with confidence propagation and metadata.

    Uses deterministic event_id when params.event_id is set, else generates from
    track/frame/subtype when track_id and frame_index are present, else uuid4.
    """
    payload = merge_metadata(params.payload, params.metadata)
    confidence = apply_confidence_floor(params.confidence)

    if params.event_id is not None:
        event_id = params.event_id
    elif params.track_id is not None and params.frame_index is not None:
        event_id = generate_event_id(
            store_id=params.store_id,
            camera_id=params.camera_id,
            event_type=params.event_type.value,
            track_id=params.track_id,
            frame_index=params.frame_index,
            subtype=params.idempotency_subtype,
        )
    else:
        event_id = generate_unique_event_id()

    event = EventEnvelope(
        schema_version=params.schema_version,
        event_id=event_id,
        event_type=params.event_type,
        store_id=params.store_id,
        camera_id=params.camera_id,
        occurred_at=params.occurred_at,
        track_id=params.track_id,
        global_person_id=params.global_person_id,
        is_staff=params.is_staff,
        confidence=confidence,
        calibration_method=params.calibration_method,
        bbox=params.bbox,
        frame_index=params.frame_index,
        payload=payload,
    )
    result = validate_event(event)
    if not result.valid:
        raise ValueError(f"Invalid event: {result.issues}")
    return event


def validate_event(event: EventEnvelope | dict[str, Any]) -> ValidationResult:
    """Validate an event against the canonical EventEnvelope schema."""
    try:
        if isinstance(event, dict):
            EventEnvelope.model_validate(event)
        else:
            EventEnvelope.model_validate(event.model_dump())
    except ValidationError as exc:
        issues = [
            ValidationIssue(field=".".join(str(x) for x in err["loc"]), message=err["msg"])
            for err in exc.errors()
        ]
        return ValidationResult.fail(*issues)

    issues: list[ValidationIssue] = []
    model = event if isinstance(event, EventEnvelope) else EventEnvelope.model_validate(event)

    if model.confidence < 0 or model.confidence > 1:
        issues.append(
            ValidationIssue(field="confidence", message="must be between 0 and 1")
        )
    if model.occurred_at.tzinfo is None:
        issues.append(
            ValidationIssue(
                field="occurred_at",
                message="should be timezone-aware",
                severity=ValidationSeverity.WARNING,
            )
        )

    if issues:
        return ValidationResult(valid=False, issues=issues)
    return ValidationResult.ok()


def validate_batch(events: list[EventEnvelope | dict[str, Any]]) -> ValidationResult:
    """Validate a batch; aggregate all issues."""
    all_issues: list[ValidationIssue] = []
    for idx, ev in enumerate(events):
        result = validate_event(ev)
        if not result.valid:
            for issue in result.issues:
                all_issues.append(
                    ValidationIssue(
                        field=f"[{idx}].{issue.field}",
                        message=issue.message,
                        severity=issue.severity,
                    )
                )
    if all_issues:
        return ValidationResult(valid=False, issues=all_issues)
    return ValidationResult.ok()


def event_to_dict(event: EventEnvelope) -> dict[str, Any]:
    return event.model_dump(mode="json")


def event_from_dict(data: dict[str, Any]) -> EventEnvelope:
    return EventEnvelope.model_validate(data)


def event_to_jsonl_line(event: EventEnvelope) -> str:
    """Serialize one event as a JSONL line (newline-terminated)."""
    return event.model_dump_json() + "\n"


def parse_jsonl_line(line: str) -> EventEnvelope:
    """Parse and validate a single JSONL line."""
    line = line.strip()
    if not line:
        raise ValueError("empty JSONL line")
    return event_from_dict(json.loads(line))


def events_to_jsonl(events: list[EventEnvelope]) -> str:
    return "".join(event_to_jsonl_line(e) for e in events)


def events_from_jsonl(text: str) -> list[EventEnvelope]:
    return list(iter_jsonl_events(text.splitlines()))


def iter_jsonl_events(lines: Iterator[str] | list[str]) -> Iterator[EventEnvelope]:
    """Lazy iterator for replay — validates each line."""
    for line_no, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            yield parse_jsonl_line(line)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise ValueError(f"Invalid JSONL at line {line_no}: {exc}") from exc


# ---------------------------------------------------------------------------
# Deduplication helpers (replay-compatible: same event_id → duplicate)
# ---------------------------------------------------------------------------


DedupKey = UUID


def dedup_key(event: EventEnvelope) -> DedupKey:
    """Primary deduplication key — event_id is stable across replays."""
    return event.event_id


def is_duplicate(event: EventEnvelope, seen: set[DedupKey]) -> bool:
    key = dedup_key(event)
    if key in seen:
        return True
    seen.add(key)
    return False


def filter_duplicates(events: list[EventEnvelope]) -> tuple[list[EventEnvelope], int]:
    """Return new events only; report duplicate count."""
    seen: set[DedupKey] = set()
    unique: list[EventEnvelope] = []
    duplicates = 0
    for ev in events:
        if is_duplicate(ev, seen):
            duplicates += 1
        else:
            unique.append(ev)
    return unique, duplicates
