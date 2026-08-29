# PROMPT:
# Heatmap engine tests — cell binning from spatial samples and empty DB path.
#
# CHANGES MADE:
# - HeatmapEngine aggregates mocked repository samples into HeatmapResponse
# - Degraded mode returns empty cells when DB unavailable

"""Heatmap analytics tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.heatmap import HeatmapEngine
from app.state import AppState


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heatmap_bins_bbox_centroid() -> None:
    session = MagicMock()
    engine = HeatmapEngine(session, AppState())
    engine._state.mark_db_up()
    engine._repo = MagicMock()
    engine._repo.fetch_spatial_samples = AsyncMock(
        return_value=[(960.0, 540.0, 10.0), (100.0, 100.0, 0.0)],
    )
    now = datetime.now(timezone.utc)
    resp = await engine.get_heatmap("store-001", now, now, resolution=32)
    assert resp.store_id == "store-001"
    assert len(resp.cells) >= 1
    assert all(c.visits >= 1 for c in resp.cells)
    assert resp.data_confidence in ("low", "medium", "high")
    peak = max(c.visits for c in resp.cells)
    assert any(c.visits_normalized == 100 for c in resp.cells if c.visits == peak)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_heatmap_empty_when_db_down() -> None:
    state = AppState()
    state.mark_db_down()
    engine = HeatmapEngine(MagicMock(), state)
    now = datetime.now(timezone.utc)
    resp = await engine.get_heatmap("store-001", now, now, 16)
    assert resp.cells == []
