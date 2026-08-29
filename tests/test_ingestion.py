# PROMPT:
# Legacy ingestion tests — retained for backward compatibility; see test_ingestion_suite.py.
#
# CHANGES MADE:
# - Redirected extended coverage to test_ingestion_suite.py and tests/db/
# - Kept core validate/dedup smoke tests with shared EventFactory fixtures

"""Legacy ingestion unit tests (see test_ingestion_suite.py for full coverage)."""

from __future__ import annotations

import pytest

from app.ingestion import validate_event_payload, split_in_batch_duplicates
from tests.factories import EventFactory

pytestmark = pytest.mark.unit


def test_validate_smoke(event_factory: EventFactory) -> None:
    env, err = validate_event_payload(event_factory.to_raw(event_factory.entry()), 0)
    assert env and not err


def test_split_dup_smoke(event_factory: EventFactory) -> None:
    e = event_factory.entry()
    u, d = split_in_batch_duplicates([e, e])
    assert len(u) == 1 and d == 1
