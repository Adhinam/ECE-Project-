# PROMPT:
# POS CSV loader and visitor conversion correlation tests.
#
# CHANGES MADE:
# - load_pos_transactions filters by store and window
# - converted_visitors_from_pos matches billing-zone presence within time window

"""POS correlation tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.pos import PosTransaction, converted_visitors_from_pos, load_pos_transactions
from schemas.api import MetricConfidence


@pytest.mark.unit
def test_load_pos_from_fixture(tmp_path: Path) -> None:
    path = tmp_path / "pos.csv"
    path.write_text(
        "store_id,transaction_id,timestamp,basket_value_inr\n"
        "store-001,t1,2026-01-15T10:00:00Z,100\n"
        "store-001,t2,2026-01-15T11:00:00Z,200\n"
        "other,t3,2026-01-15T11:00:00Z,300\n",
        encoding="utf-8",
    )
    now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    rows = load_pos_transactions(
        path,
        store_id="store-001",
        from_time=now - timedelta(hours=6),
        to_time=now,
    )
    assert len(rows) >= 2
    assert all(r.store_id == "store-001" for r in rows)


@pytest.mark.unit
def test_converted_visitors_window_match() -> None:
    txns = [
        PosTransaction("t1", "s", datetime(2026, 3, 3, 14, 38, 12, tzinfo=timezone.utc), 10.0),
    ]
    billing_last_seen = {
        "VIS_aaa111": datetime(2026, 3, 3, 14, 36, 0, tzinfo=timezone.utc),
        "VIS_ccc333": datetime(2026, 3, 3, 14, 20, 0, tzinfo=timezone.utc),
    }
    converted, conf = converted_visitors_from_pos(
        transactions=txns,
        billing_last_seen=billing_last_seen,
        match_window=timedelta(minutes=5),
    )
    assert converted == {"VIS_aaa111"}
    assert conf == MetricConfidence.LOW


def test_one_sale_cannot_convert_multiple_nearby_visitors():
    now = datetime(2026, 3, 3, 14, 38, tzinfo=timezone.utc)
    converted, confidence = converted_visitors_from_pos(
        transactions=[PosTransaction("t1", "s", now)],
        billing_last_seen={"a": now, "b": now - timedelta(seconds=20)},
        match_window=timedelta(minutes=5),
    )
    assert converted == set()
    assert confidence == MetricConfidence.LOW


def test_transaction_volume_does_not_certify_identity():
    now = datetime(2026, 3, 3, 14, 38, tzinfo=timezone.utc)
    converted, confidence = converted_visitors_from_pos(
        transactions=[PosTransaction(str(i), "s", now) for i in range(12)],
        billing_last_seen={"a": now},
        match_window=timedelta(minutes=5),
    )
    assert converted == {"a"}
    assert confidence == MetricConfidence.LOW
