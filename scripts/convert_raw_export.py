"""Convert psql json_agg export into challenge events.jsonl."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schemas.challenge_events import envelope_to_challenge
from schemas.events import EventEnvelope, EventType


def _parse_ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def convert_rows(
    rows: list[dict],
    store_id: str,
    events_root: Path,
    *,
    merged_only: bool = False,
) -> Path:
    by_camera: dict[str, list[str]] = defaultdict(list)
    merged: list[str] = []
    date_part: str | None = None

    for row in rows:
        payload = dict(row.get("payload") or {})
        if row.get("global_person_id") and "visitor_id" not in payload:
            payload["visitor_id"] = row["global_person_id"]
        occurred = _parse_ts(row["occurred_at"])
        if date_part is None:
            date_part = occurred.strftime("%Y-%m-%d")
        envelope = EventEnvelope(
            event_id=UUID(str(row["event_id"])),
            event_type=EventType(str(row["event_type"])),
            store_id=str(row["store_id"]),
            camera_id=str(row["camera_id"]),
            occurred_at=occurred,
            track_id=row.get("track_id"),
            global_person_id=row.get("global_person_id"),
            is_staff=bool(row.get("is_staff")),
            confidence=float(row["confidence"]),
            payload=payload,
            schema_version=int(row.get("schema_version") or 1),
        )
        line = envelope_to_challenge(envelope).model_dump_json() + "\n"
        merged.append(line)
        by_camera[str(row["camera_id"])].append(line)

    store_dir = events_root / store_id
    store_dir.mkdir(parents=True, exist_ok=True)
    merged_path = store_dir / "events.jsonl"
    merged_path.write_text("".join(merged), encoding="utf-8")

    if date_part and not merged_only:
        cam_dir = store_dir / date_part
        cam_dir.mkdir(parents=True, exist_ok=True)
        for camera_id, lines in sorted(by_camera.items()):
            (cam_dir / f"{camera_id}.jsonl").write_text("".join(lines), encoding="utf-8")

    return merged_path


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Convert psql json_agg export into challenge JSONL")
    parser.add_argument("raw_json", type=Path)
    parser.add_argument("store_id")
    parser.add_argument("events_root", type=Path)
    parser.add_argument(
        "--merged-only",
        action="store_true",
        help="Write only data/events/{store}/events.jsonl",
    )
    args = parser.parse_args()
    text = args.raw_json.read_text(encoding="utf-8-sig").strip()
    rows = json.loads(text)
    path = convert_rows(rows, args.store_id, args.events_root, merged_only=args.merged_only)
    print(f"Wrote {len(rows)} events -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
