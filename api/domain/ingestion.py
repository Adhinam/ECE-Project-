"""Ingestion domain rules — classify accept/duplicate/reject per event."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from schemas.events import EventEnvelope


@dataclass(frozen=True)
class IngestionResult:
    accepted: int
    duplicates: int
    rejected: int


def partition_events(
    events: list[EventEnvelope],
    existing_ids: set[UUID],
) -> tuple[list[EventEnvelope], list[EventEnvelope], list[EventEnvelope]]:
    """Split events into new, duplicate, and invalid (placeholder validation hook)."""
    new: list[EventEnvelope] = []
    duplicates: list[EventEnvelope] = []
    # TODO: add validation failures → rejected
    for event in events:
        if event.event_id in existing_ids:
            duplicates.append(event)
        else:
            new.append(event)
    return new, duplicates, []
