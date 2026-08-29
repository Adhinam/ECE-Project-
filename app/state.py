"""Process-wide runtime state — health and graceful degradation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class AppState:
    """
    Mutable runtime flags updated during application lifespan.

    Attached to FastAPI app.state.runtime for access in routes and dependencies.
    """

    db_available: bool = False
    degraded_features: list[str] = field(default_factory=list)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_db_check_at: datetime | None = None
    pipeline_last_event_at: datetime | None = None

    def mark_db_up(self) -> None:
        self.db_available = True
        self.degraded_features = [f for f in self.degraded_features if f != "postgres"]
        self.last_db_check_at = datetime.now(timezone.utc)

    def mark_db_down(self, reason: str = "postgres") -> None:
        self.db_available = False
        if reason not in self.degraded_features:
            self.degraded_features.append(reason)

    def note_pipeline_events(self, occurred_times: list[datetime]) -> None:
        """Advance in-memory pipeline watermark after successful ingest."""
        if not occurred_times:
            return
        latest = max(occurred_times)
        if self.pipeline_last_event_at is None or latest > self.pipeline_last_event_at:
            self.pipeline_last_event_at = latest

    @property
    def status(self) -> str:
        if self.db_available and not self.degraded_features:
            return "ok"
        if self.db_available:
            return "degraded"
        return "down"
