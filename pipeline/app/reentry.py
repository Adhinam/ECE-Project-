"""Re-entry detection after exit within configured window."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from schemas.config import StoreLayoutConfig
from schemas.events import EventEnvelope


@dataclass
class ExitRecord:
    global_person_id: str
    exit_event_id: str
    occurred_at: datetime


class ReentryDetector:
    def __init__(self, layout: StoreLayoutConfig) -> None:
        self._window = timedelta(seconds=layout.reentry_window_seconds)
        self._recent_exits: dict[str, ExitRecord] = {}

    def on_exit(self, global_person_id: str, exit_event_id: str, occurred_at: datetime) -> None:
        self._recent_exits[global_person_id] = ExitRecord(
            global_person_id=global_person_id,
            exit_event_id=exit_event_id,
            occurred_at=occurred_at,
        )

    def on_entry(self, global_person_id: str, occurred_at: datetime) -> EventEnvelope | None:
        record = self._recent_exits.get(global_person_id)
        if record is None:
            return None
        if occurred_at - record.occurred_at > self._window:
            return None
        # TODO: build reentry EventEnvelope
        return None
