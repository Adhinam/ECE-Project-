"""Export ingested events from Postgres to challenge JSONL (no inference)."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schemas.challenge_events import envelope_to_challenge
from schemas.events import EventEnvelope, EventType

DEFAULT_DSN = "postgresql://store_intel:store_intel_dev@localhost:5432/store_intel"


def _row_to_envelope(row: asyncpg.Record) -> EventEnvelope:
    payload = dict(row["payload"] or {})
    if row["global_person_id"] and "visitor_id" not in payload:
        payload["visitor_id"] = row["global_person_id"]
    return EventEnvelope(
        event_id=UUID(str(row["event_id"])),
        event_type=EventType(str(row["event_type"])),
        store_id=str(row["store_id"]),
        camera_id=str(row["camera_id"]),
        occurred_at=row["occurred_at"],
        track_id=row["track_id"],
        global_person_id=row["global_person_id"],
        is_staff=bool(row["is_staff"]),
        confidence=float(row["confidence"]),
        payload=payload,
        schema_version=int(row["schema_version"] or 1),
    )


async def export_store(
    conn: asyncpg.Connection,
    store_id: str,
    events_root: Path,
    *,
    write_per_camera: bool = False,
) -> tuple[Path, int]:
    rows = await conn.fetch(
        """
        SELECT event_id, event_type, store_id, camera_id, occurred_at,
               payload, confidence, global_person_id, track_id, is_staff, schema_version
        FROM raw_events
        WHERE store_id = $1
        ORDER BY occurred_at ASC, camera_id ASC
        """,
        store_id,
    )
    if not rows:
        raise RuntimeError(f"No events in database for store_id={store_id!r}")

    by_camera: dict[str, list[str]] = defaultdict(list)
    merged_lines: list[str] = []

    for row in rows:
        challenge = envelope_to_challenge(_row_to_envelope(row))
        line = challenge.model_dump_json() + "\n"
        merged_lines.append(line)
        by_camera[str(row["camera_id"])].append(line)

    date_part = rows[0]["occurred_at"].astimezone(timezone.utc).strftime("%Y-%m-%d")
    store_dir = events_root / store_id
    store_dir.mkdir(parents=True, exist_ok=True)

    merged_path = store_dir / "events.jsonl"
    merged_path.write_text("".join(merged_lines), encoding="utf-8")

    if write_per_camera:
        cam_dir = store_dir / date_part
        cam_dir.mkdir(parents=True, exist_ok=True)
        for camera_id, lines in sorted(by_camera.items()):
            (cam_dir / f"{camera_id}.jsonl").write_text("".join(lines), encoding="utf-8")

    return merged_path, len(rows)


async def main_async(args: argparse.Namespace) -> int:
    stores = args.store or ["store1", "store2"]
    events_root = Path(args.events_root)
    conn = await asyncpg.connect(args.dsn)
    try:
        for store_id in stores:
            path, count = await export_store(conn, store_id, events_root)
            print(f"Exported {count} events -> {path}")
    finally:
        await conn.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Export DB events to challenge JSONL")
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--events-root", default=str(ROOT / "data" / "events"))
    parser.add_argument("--store", action="append", help="Store id (repeatable; default store1+store2)")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
