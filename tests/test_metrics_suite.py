# PROMPT:
# Metrics engine tests — session aggregation, empty store, staff exclusion, zero purchase.
#
# CHANGES MADE:
# - compute_session_metrics edge cases via VisitorJourney factory
# - build_zone_metrics / empty_store_response confidence paths
# - StoreMetricsEngine degraded mode without DB

"""Store metrics engine tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.metrics import (
    StoreMetricsEngine,
    build_zone_metrics,
    compute_session_metrics,
    empty_store_response,
)
from app.state import AppState
from schemas.api import MetricConfidence
from tests.factories import EventFactory, VisitorJourney


@pytest.mark.unit
class TestSessionMetrics:
    def test_empty_events(self) -> None:
        m = compute_session_metrics([], checkout_zone_id="checkout")
        assert m.unique_visitors == 0 and m.conversion_rate == 0.0

    def test_zero_purchase_store(self, visitor_journey: VisitorJourney) -> None:
        visitor_journey.enter().visit_zone("entrance-lobby").leave()
        m = compute_session_metrics(visitor_journey.to_session_rows(), checkout_zone_id="checkout")
        assert m.unique_visitors == 1
        assert m.conversion_rate == 0.0
        assert m.has_purchases is False

    def test_conversion_with_checkout(self, visitor_journey: VisitorJourney) -> None:
        (
            visitor_journey.enter()
            .visit_zone("entrance-lobby")
            .join_queue()
            .checkout()
            .leave()
        )
        m = compute_session_metrics(visitor_journey.to_session_rows(), checkout_zone_id="checkout")
        assert m.conversion_rate == 1.0
        assert m.has_purchases is True

    def test_staff_excluded_from_counts(self, event_factory: EventFactory) -> None:
        from app.metrics import SessionEventRow

        rows = [
            SessionEventRow(
                event_type="entry",
                occurred_at=datetime.now(timezone.utc),
                track_id=1,
                global_person_id="t1",
                is_staff=False,
                confidence=0.9,
                payload={},
            ),
            SessionEventRow(
                event_type="entry",
                occurred_at=datetime.now(timezone.utc),
                track_id=2,
                global_person_id="staff",
                is_staff=True,
                confidence=0.9,
                payload={},
            ),
        ]
        m = compute_session_metrics(rows, checkout_zone_id="checkout")
        assert m.unique_visitors == 1
        assert m.staff_excluded == 1


@pytest.mark.unit
class TestZoneMetrics:
    def test_empty_zones(self) -> None:
        zones, conf = build_zone_metrics([])
        assert zones == [] and conf == MetricConfidence.UNAVAILABLE

    def test_high_sample_confidence(self) -> None:
        rows = [("lobby", 30.0, 12, 0.9)]
        _, conf = build_zone_metrics(rows)
        assert conf == MetricConfidence.HIGH


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_degraded_without_db(app_state_db_down: AppState) -> None:
    engine = StoreMetricsEngine(MagicMock(), app_state_db_down)
    resp = await engine.get_store_metrics("store-001")
    assert resp.is_empty is True
    assert resp.unique_visitors == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_with_mock_repo(app_state: AppState) -> None:
    engine = StoreMetricsEngine(MagicMock(), app_state)
    engine._repo = MagicMock()
    engine._repo.fetch_all = AsyncMock(
        return_value=(False, [], [], [], 0),
    )
    resp = await engine.get_store_metrics("store-001")
    assert resp.is_empty is True
