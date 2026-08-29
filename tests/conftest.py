# PROMPT:
# Shared pytest fixtures — factories, app state, mock DB session, FastAPI client,
# and in-memory repository for isolated tests.
#
# CHANGES MADE:
# - EventFactory + VisitorJourney fixtures
# - mock_async_session with working begin() context manager
# - memory_event_repository for isolated ingestion persistence tests
# - create_app TestClient fixture (unit; no live Postgres required)

"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.state import AppState
from schemas.events import EventEnvelope
from tests.db.memory_repository import MemoryEventRepository, MemoryEventStore
from tests.factories import EventFactory, VisitorJourney


@pytest.fixture
def app_state() -> AppState:
    state = AppState()
    state.mark_db_up()
    return state


@pytest.fixture
def app_state_db_down() -> AppState:
    state = AppState()
    state.mark_db_down("postgres")
    return state


@pytest.fixture
def event_factory() -> EventFactory:
    return EventFactory(
        base_time=datetime(2026, 5, 29, 10, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def visitor_journey(event_factory: EventFactory) -> VisitorJourney:
    return VisitorJourney(factory=event_factory)


@pytest.fixture
def memory_store() -> MemoryEventStore:
    return MemoryEventStore()


@pytest.fixture
def memory_event_repository(memory_store: MemoryEventStore) -> MemoryEventRepository:
    return MemoryEventRepository(memory_store)


@pytest.fixture
def mock_async_session() -> MagicMock:
    """SQLAlchemy-like async session mock with transaction context."""

    class _BeginContext:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, *args: object) -> None:
            return None

    session = MagicMock()
    session.begin = MagicMock(return_value=_BeginContext())
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.flush = AsyncMock()
    session.close = AsyncMock()
    return session


@pytest.fixture
def api_client() -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def sample_event(event_factory: EventFactory) -> EventEnvelope:
    """Canonical sample event for domain/contract tests."""
    return event_factory.entry(track_id=1, frame_index=100)
