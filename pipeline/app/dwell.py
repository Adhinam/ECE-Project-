"""Dwell time tracking per (person, zone)."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic

from schemas.config import StoreLayoutConfig
from schemas.events import EventEnvelope


@dataclass
class DwellState:
    entered_at: float = field(default_factory=monotonic)
    zone_id: str = ""


class DwellTracker:
    def __init__(self, layout: StoreLayoutConfig) -> None:
        self._threshold = layout.dwell_threshold_seconds
        self._states: dict[tuple[str, str], DwellState] = {}

    def update(self, global_person_id: str, zone_id: str, inside: bool) -> EventEnvelope | None:
        key = (global_person_id, zone_id)
        # TODO: implement dwell FSM
        _ = (key, inside, self._threshold, self._states)
        return None
