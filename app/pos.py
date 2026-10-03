"""POS transaction loader and visitor-session correlation for conversion metrics.

Source of truth: Purplle challenge PDF.

POS input schema (no customer identity):
    store_id, transaction_id, timestamp, basket_value_inr

Correlation rule:
    Only an unambiguous billing-window candidate is counted as an estimated
    conversion. Time proximity alone is never a confirmed purchase.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from schemas.api import MetricConfidence
from shared.logging import get_logger

logger = get_logger(__name__)


class PosSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="POS_")

    transactions_path: Path = Field(
        default=Path("data/pos_transactions.csv"),
        description="CSV with store_id, transaction_id, timestamp, basket_value_inr (challenge schema)",
    )
    match_window_minutes: int = Field(
        default=5,
        ge=1,
        description="Time window (minutes) before txn for billing-zone presence",
    )


@dataclass(frozen=True)
class PosTransaction:
    transaction_id: str
    store_id: str
    timestamp: datetime
    basket_value_inr: float = 0.0


def _parse_timestamp(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)


def _float_or_zero(v: object) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def load_pos_transactions(
    path: Path,
    *,
    store_id: str,
    from_time: datetime,
    to_time: datetime,
) -> list[PosTransaction]:
    """Load POS rows for a store within [from_time, to_time]. Missing file → []."""
    if not path.is_file():
        return []

    rows: list[PosTransaction] = []
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            if row.get("store_id") != store_id:
                continue
            try:
                ts = _parse_timestamp(str(row["timestamp"]))
            except (KeyError, ValueError):
                continue
            if ts < from_time or ts > to_time:
                continue
            rows.append(
                PosTransaction(
                    transaction_id=str(row.get("transaction_id", "")),
                    store_id=store_id,
                    timestamp=ts,
                    basket_value_inr=_float_or_zero(
                        row.get("basket_value_inr", row.get("amount", 0.0))
                    ),
                )
            )
    return rows


def converted_visitors_from_pos(
    *,
    transactions: list[PosTransaction],
    billing_last_seen: dict[str, datetime],
    match_window: timedelta,
) -> tuple[set[str], MetricConfidence]:
    """
    Match POS transactions to visitor sessions using billing-zone presence.

    Conservatively estimate conversion only when exactly one visitor is eligible
    in the billing window. Ambiguous matches are left unresolved. These estimates
    are always low-confidence without an explicit transaction/session identity.
    """
    if not transactions:
        return set(), MetricConfidence.UNAVAILABLE
    if not billing_last_seen:
        return set(), MetricConfidence.UNAVAILABLE

    converted: set[str] = set()
    window_s = match_window.total_seconds()
    seen_transactions: set[tuple[str, str]] = set()
    for txn in sorted(transactions, key=lambda item: item.timestamp):
        key = (txn.store_id, txn.transaction_id)
        if key in seen_transactions:
            continue
        seen_transactions.add(key)
        candidates = [
            visitor_id for visitor_id, last_seen in billing_last_seen.items()
            if 0.0 <= (txn.timestamp - last_seen).total_seconds() <= window_s
        ]
        if len(candidates) == 1:
            converted.add(candidates[0])
    return converted, MetricConfidence.LOW
