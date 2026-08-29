"""Re-export merged events.jsonl from Postgres (no inference, merged files only)."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.convert_raw_export import convert_rows

EXPORT_SQL = """
SELECT coalesce(json_agg(row_to_json(r) ORDER BY occurred_at, camera_id), '[]'::json)
FROM (
  SELECT event_id::text, event_type, store_id, camera_id, occurred_at,
         payload, confidence, global_person_id, track_id, is_staff, schema_version
  FROM raw_events
  WHERE store_id = '{store_id}'
) r;
"""


def _fetch_store_rows(store_id: str) -> list[dict]:
    sql = EXPORT_SQL.format(store_id=store_id)
    result = subprocess.run(
        [
            "docker",
            "exec",
            "store-intel-postgres",
            "psql",
            "-U",
            "store_intel",
            "-d",
            "store_intel",
            "-t",
            "-A",
            "-c",
            sql,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout.strip())


def main() -> int:
    events_root = ROOT / "data" / "events"
    stores = ["store1", "store2"]
    for store_id in stores:
        rows = _fetch_store_rows(store_id)
        path = convert_rows(rows, store_id, events_root, merged_only=True)
        print(f"Exported {len(rows)} events -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
