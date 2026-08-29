# PROMPT:
# Domain ingestion tests — see test_domain_suite.py.
#
# CHANGES MADE:
# - partition_events duplicate/new paths via EventFactory

"""Domain ingestion tests."""

from __future__ import annotations

import pytest

from api.domain.ingestion import partition_events

pytestmark = pytest.mark.unit


def test_partition_duplicate(sample_event) -> None:
    new, dups, rejected = partition_events([sample_event], {sample_event.event_id})
    assert len(new) == 0 and len(dups) == 1 and len(rejected) == 0


def test_partition_new(sample_event) -> None:
    new, dups, _ = partition_events([sample_event], set())
    assert len(new) == 1 and len(dups) == 0
