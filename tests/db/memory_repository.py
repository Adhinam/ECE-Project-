# PROMPT:
# In-memory event store mimicking EventRepository for isolated DB-free pytest runs.
#
# CHANGES MADE:
# - MemoryEventStore tracks raw events and ingest batches without SQLAlchemy
# - MemoryEventRepository mirrors app.repositories.EventRepository async API

"""In-memory persistence for isolated ingestion tests."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from schemas.events import EventEnvelope


class MemoryEventStore:
    def __init__(self) -> None:
        self.events: dict[UUID, EventEnvelope] = {}
        self.batches: list[dict] = []

    def clear(self) -> None:
        self.events.clear()
        self.batches.clear()


class MemoryEventRepository:
    """Drop-in test double for EventRepository."""

    def __init__(self, store: MemoryEventStore) -> None:
        self._store = store
        self._session = None  # API compatibility

    async def find_existing_ids(self, event_ids: list[UUID]) -> set[UUID]:
        return {eid for eid in event_ids if eid in self._store.events}

    async def insert_events(
        self,
        events: list[EventEnvelope],
        *,
        commit: bool = True,
    ) -> int:
        now = datetime.now(timezone.utc)
        for e in events:
            self._store.events[e.event_id] = e
        return len(events)

    async def record_ingest_batch(
        self,
        batch_id: UUID,
        *,
        accepted: int,
        duplicates: int,
        rejected: int,
        commit: bool = True,
    ) -> None:
        self._store.batches.append(
            {
                "batch_id": batch_id,
                "received_at": datetime.now(timezone.utc),
                "accepted": accepted,
                "duplicates": duplicates,
                "rejected": rejected,
            }
        )

    async def get_latest_event_time(self, store_id: str):
        times = [e.occurred_at for e in self._store.events.values() if e.store_id == store_id]
        return max(times) if times else None
