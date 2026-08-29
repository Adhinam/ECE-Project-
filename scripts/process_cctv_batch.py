"""Run all challenge CCTV clips through pipeline and produce validation report."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "cctv_batch_config.json"
LAYOUT_PATH = ROOT / "configs" / "store_layout_cctv.json"
EVENTS_ROOT = ROOT / "data" / "events"
MERGED_EVENTS_PATH = EVENTS_ROOT / "events.jsonl"
REPORT_PATH = EVENTS_ROOT / "validation_report.json"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_video(store_id: str, camera_id: str, video_path: str) -> None:
    env = os.environ.copy()
    env.update(
        {
            "PIPELINE_STORE_ID": store_id,
            "PIPELINE_CAMERA_ID": camera_id,
            "PIPELINE_VIDEO_SOURCE": video_path,
            "STORE_LAYOUT_PATH": str(LAYOUT_PATH),
            "EVENTS_OUTPUT_DIR": str(EVENTS_ROOT),
            "ENABLE_REID": "true",
            "DEGRADE_REID": "false",
            "PIPELINE_FPS_LIMIT": "0",
            "EMIT_CHALLENGE_FORMAT": "true",
            "MODEL_PATH": "yolov8n.pt",
        }
    )
    cmd = [sys.executable, "-m", "pipeline.main", "run"]
    print(f"RUNNING: {' '.join(cmd)} | camera={camera_id} | video={video_path}", flush=True)
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def _collect_event_files(store_id: str) -> list[Path]:
    store_dir = EVENTS_ROOT / store_id
    if not store_dir.exists():
        return []
    return sorted(store_dir.rglob("*.jsonl"))


def _merge_jsonl(files: list[Path], output_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for file_path in files:
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    return rows


def _event_type_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row.get("event_type", ""))] += 1
    return dict(counts)


def _zone_dwell_stats(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    by_zone: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if str(row.get("event_type")) != "ZONE_DWELL":
            continue
        zone_id = str(row.get("zone_id") or "UNKNOWN")
        dwell_ms = float(row.get("dwell_ms") or 0.0)
        by_zone[zone_id].append(dwell_ms)
    out: dict[str, dict[str, float]] = {}
    for zone_id, values in by_zone.items():
        if not values:
            continue
        out[zone_id] = {
            "count": float(len(values)),
            "avg_dwell_ms": float(sum(values) / len(values)),
            "max_dwell_ms": float(max(values)),
        }
    return out


def build_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _event_type_counts(rows)
    unique_visitors = {
        str(r.get("visitor_id"))
        for r in rows
        if r.get("visitor_id") is not None
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_events": len(rows),
        "total_ENTRY_events": counts.get("ENTRY", 0),
        "total_EXIT_events": counts.get("EXIT", 0),
        "total_unique_visitors": len(unique_visitors),
        "total_REENTRY_events": counts.get("REENTRY", 0),
        "total_BILLING_QUEUE_JOIN_events": counts.get("BILLING_QUEUE_JOIN", 0),
        "total_BILLING_QUEUE_ABANDON_events": counts.get("BILLING_QUEUE_ABANDON", 0),
        "zone_dwell_statistics": _zone_dwell_stats(rows),
        "event_type_counts": counts,
    }


def main() -> int:
    cfg = _load_json(CONFIG_PATH)
    store_id = str(cfg["store_id"])
    videos = cfg["videos"]

    for item in videos:
        _run_video(store_id, str(item["camera_id"]), str(item["video_path"]))

    files = _collect_event_files(store_id)
    if not files:
        raise RuntimeError("No JSONL event files produced under data/events")

    print("EVENT FILES:", *[str(f) for f in files], sep="\n  ", flush=True)
    rows = _merge_jsonl(files, MERGED_EVENTS_PATH)
    if not rows:
        raise RuntimeError("Events were produced but merged output is empty")

    report = build_report(rows)
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"MERGED_EVENTS_PATH={MERGED_EVENTS_PATH}")
    print(f"REPORT_PATH={REPORT_PATH}")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
