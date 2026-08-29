# PROMPT:
# Production health endpoint tests — DB down, stale feed, ingestion lag, per-store rollup.
#
# CHANGES MADE:
# - HealthChecker unit tests with mocked HealthDataRepository
# - build_store_health and compute_overall_status matrices
# - FastAPI /health integration shape validation

"""Health checker comprehensive tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.health import (
    HealthChecker,
    HealthThresholds,
    _StoreSignals,
    build_store_health,
    compute_overall_status,
)
from app.state import AppState


@pytest.mark.unit
class TestOverallStatus:
    def test_down_when_db_down(self) -> None:
        assert (
            compute_overall_status(
                db_status="down",
                ingestion_status="ok",
                global_stale=False,
                store_statuses=[],
                degraded_features=[],
            )
            == "down"
        )

    def test_ok_when_all_clear(self) -> None:
        assert (
            compute_overall_status(
                db_status="up",
                ingestion_status="ok",
                global_stale=False,
                store_statuses=["ok", "unknown"],
                degraded_features=[],
            )
            == "ok"
        )


@pytest.mark.unit
class TestStoreHealthBuilder:
    def test_stale_store(self) -> None:
        now = datetime.now(timezone.utc)
        h = build_store_health(
            _StoreSignals(
                store_id="store-001",
                latest_event_at=now - timedelta(minutes=30),
            ),
            now=now,
            thresholds=HealthThresholds(),
        )
        assert h.stale_feed is True

    def test_stale_cameras_listed(self) -> None:
        now = datetime.now(timezone.utc)
        h = build_store_health(
            _StoreSignals(
                "store-001",
                latest_event_at=now - timedelta(minutes=1),
                cameras={"cam-a": now - timedelta(minutes=40)},
            ),
            now=now,
            thresholds=HealthThresholds(),
        )
        assert "cam-a" in h.stale_cameras


@pytest.mark.unit
@pytest.mark.asyncio
class TestHealthChecker:
    async def test_db_down_response(self, app_state_db_down: AppState) -> None:
        checker = HealthChecker(None, app_state_db_down)
        with patch("app.health.check_db_connection", AsyncMock(return_value=False)):
            resp = await checker.check()
        assert resp.status == "down"
        assert resp.database.status == "down"
        assert resp.stale_feed is True

    async def test_full_check_with_mocks(self, app_state: AppState) -> None:
        now = datetime.now(timezone.utc)
        checker = HealthChecker(MagicMock(), app_state)
        mock_repo = MagicMock()
        mock_repo.fetch_global_timestamps = AsyncMock(return_value=(now, now))
        mock_repo.fetch_latest_ingest_batch_time = AsyncMock(return_value=now)
        mock_repo.count_ingest_batches_since = AsyncMock(return_value=3)
        mock_repo.fetch_per_store_timestamps = AsyncMock(
            return_value=[("store-001", now, now)],
        )
        mock_repo.fetch_camera_last_events = AsyncMock(
            return_value=[("store-001", "cam-1", now)],
        )

        with (
            patch("app.health.HealthDataRepository", return_value=mock_repo),
            patch("app.health.check_db_connection", AsyncMock(return_value=True)),
        ):
            resp = await checker.check()

        assert resp.status in ("ok", "degraded")
        assert resp.database.status == "up"
        assert len(resp.stores) == 1
        assert any(c.name == "database" for c in resp.checks)


@pytest.mark.integration
def test_health_route_schema(api_client) -> None:
    r = api_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded", "down")
    assert "database" in body and "checks" in body
