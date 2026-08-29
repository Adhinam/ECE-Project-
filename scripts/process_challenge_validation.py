"""Run challenge dataset videos, validate events, ingest to API, and write reports."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "challenge_batch_config.json"
REPORT_JSON = ROOT / "data" / "events" / "validation_report.json"
REPORT_MD = ROOT / "data" / "events" / "validation_report.md"
FINAL_MD = ROOT / "FINAL_DATASET_VALIDATION.md"
API_URL = os.environ.get("INGEST_API_URL", "http://localhost:8000")

REQUIRED_EVENT_TYPES = frozenset(
    {
        "ENTRY",
        "EXIT",
        "ZONE_ENTER",
        "ZONE_EXIT",
        "ZONE_DWELL",
        "BILLING_QUEUE_JOIN",
        "BILLING_QUEUE_ABANDON",
        "REENTRY",
    }
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _layout_for_role(store_cfg: dict[str, Any], role: str) -> Path:
    if role == "zone":
        return ROOT / store_cfg["layout_zone"]
    if role == "entry":
        return ROOT / store_cfg["layout_entry"]
    return ROOT / store_cfg["layout_billing"]


def _run_video(
    store_id: str,
    camera_id: str,
    video_path: str,
    layout_path: Path,
    events_root: Path,
) -> None:
    env = os.environ.copy()
    env.update(
        {
            "PIPELINE_STORE_ID": store_id,
            "PIPELINE_CAMERA_ID": camera_id,
            "PIPELINE_VIDEO_SOURCE": video_path,
            "STORE_LAYOUT_PATH": str(layout_path),
            "EVENTS_OUTPUT_DIR": str(events_root),
            "ENABLE_REID": "true",
            "DEGRADE_REID": "false",
            "PIPELINE_FPS_LIMIT": "0",
            "EMIT_CHALLENGE_FORMAT": "true",
            "MODEL_PATH": str(ROOT / "yolov8n.pt"),
        }
    )
    cmd = [sys.executable, "-m", "pipeline.main", "run"]
    print(f"RUNNING: camera={camera_id} video={video_path}", flush=True)
    subprocess.run(cmd, cwd=ROOT, env=env, check=True)


def _done_marker(store_id: str, camera_id: str, events_root: Path) -> Path:
    return events_root / store_id / ".done" / f"{camera_id}.marker"


def _camera_run_complete(store_id: str, camera_id: str, events_root: Path) -> bool:
    return _done_marker(store_id, camera_id, events_root).is_file()


def _mark_camera_done(store_id: str, camera_id: str, events_root: Path) -> None:
    marker = _done_marker(store_id, camera_id, events_root)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")


def _reset_incomplete_camera_output(store_id: str, camera_id: str, events_root: Path) -> None:
    """Remove partial JSONL when a prior run stopped before the done marker."""
    if _camera_run_complete(store_id, camera_id, events_root):
        return
    store_dir = events_root / store_id
    if not store_dir.exists():
        return
    for path in store_dir.rglob(f"{camera_id}.jsonl"):
        path.unlink()


def _collect_camera_files(store_id: str, events_root: Path) -> list[Path]:
    store_dir = events_root / store_id
    if not store_dir.exists():
        return []
    return sorted(p for p in store_dir.rglob("*.jsonl") if p.name != "events.jsonl")


def _read_events(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _merge_jsonl(rows: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")


def _event_type_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row.get("event_type", ""))] += 1
    return dict(counts)


def _per_camera_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    by_cam: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        cam = str(row.get("camera_id", "unknown"))
        by_cam[cam][str(row.get("event_type", ""))] += 1
    return {cam: dict(types) for cam, types in by_cam.items()}


def _zone_dwell_stats(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    by_zone: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if str(row.get("event_type")) != "ZONE_DWELL":
            continue
        zone_id = str(row.get("zone_id") or "UNKNOWN")
        by_zone[zone_id].append(float(row.get("dwell_ms") or 0.0))
    out: dict[str, dict[str, float]] = {}
    for zone_id, values in by_zone.items():
        out[zone_id] = {
            "count": float(len(values)),
            "avg_dwell_ms": float(sum(values) / len(values)),
            "max_dwell_ms": float(max(values)),
        }
    return out


def _build_store_report(store_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _event_type_counts(rows)
    visitors = {
        str(r.get("visitor_id"))
        for r in rows
        if r.get("visitor_id") is not None
    }
    present = REQUIRED_EVENT_TYPES & set(counts.keys())
    missing = sorted(REQUIRED_EVENT_TYPES - present)
    return {
        "store_id": store_id,
        "total_events": len(rows),
        "total_visitors": len(visitors),
        "entries": counts.get("ENTRY", 0),
        "exits": counts.get("EXIT", 0),
        "reentries": counts.get("REENTRY", 0),
        "queue_join": counts.get("BILLING_QUEUE_JOIN", 0),
        "queue_abandon": counts.get("BILLING_QUEUE_ABANDON", 0),
        "all_required_event_types_present": len(missing) == 0,
        "missing_required_types": missing,
        "event_type_counts": counts,
        "zone_dwell_statistics": _zone_dwell_stats(rows),
        "per_camera_event_counts": _per_camera_counts(rows),
    }


def _render_markdown(reports: dict[str, dict[str, Any]], generated_at: str) -> str:
    lines = [
        "# Validation Report",
        "",
        f"Generated: {generated_at}",
        "",
    ]
    for store_id, rep in reports.items():
        lines.extend(
            [
                f"## {store_id}",
                "",
                f"- **Total events:** {rep['total_events']}",
                f"- **Total visitors:** {rep['total_visitors']}",
                f"- **Entries:** {rep['entries']}",
                f"- **Exits:** {rep['exits']}",
                f"- **Reentries:** {rep['reentries']}",
                f"- **Queue join:** {rep['queue_join']}",
                f"- **Queue abandon:** {rep['queue_abandon']}",
                f"- **All required event types present:** {rep['all_required_event_types_present']}",
            ]
        )
        if rep.get("missing_required_types"):
            lines.append(f"- **Missing types:** {', '.join(rep['missing_required_types'])}")
        lines.extend(["", "### Event type counts", "", "| Event type | Count |", "|------------|------:|"])
        for et, count in sorted(rep["event_type_counts"].items()):
            lines.append(f"| {et} | {count} |")
        lines.extend(["", "### Dwell statistics", "", "| Zone | Count | Avg dwell (ms) | Max dwell (ms) |", "|------|------:|---------------:|---------------:|"])
        for zone_id, stats in rep.get("zone_dwell_statistics", {}).items():
            lines.append(
                f"| {zone_id} | {int(stats['count'])} | {stats['avg_dwell_ms']:.0f} | {stats['max_dwell_ms']:.0f} |"
            )
        lines.extend(["", "### Per-camera event counts", ""])
        for cam, types in rep.get("per_camera_event_counts", {}).items():
            lines.append(f"**{cam}**")
            for et, count in sorted(types.items()):
                lines.append(f"- {et}: {count}")
            lines.append("")
    return "\n".join(lines) + "\n"


async def _ingest_store(rows: list[dict[str, Any]], batch_size: int = 500) -> dict[str, int]:
    totals = {"accepted": 0, "duplicates": 0, "rejected": 0}
    async with httpx.AsyncClient(timeout=60.0) as client:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            response = await client.post(
                f"{API_URL.rstrip('/')}/events/ingest",
                json={"events": batch},
            )
            response.raise_for_status()
            body = response.json()
            totals["accepted"] += int(body.get("accepted", 0))
            totals["duplicates"] += int(body.get("duplicates", 0))
            totals["rejected"] += int(body.get("rejected", 0))
    return totals


async def _fetch_api_metrics(store_id: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        base = API_URL.rstrip("/")
        window = 10080
        out: dict[str, Any] = {}
        metrics = await client.get(f"{base}/stores/{store_id}/metrics", params={"window_minutes": window})
        metrics.raise_for_status()
        out["metrics"] = metrics.json()
        funnel = await client.get(
            f"{base}/stores/{store_id}/funnel",
            params={"window_minutes": window, "checkout_zone_id": "checkout", "billing_queue_id": "checkout-1"},
        )
        funnel.raise_for_status()
        out["funnel"] = funnel.json()
        heatmap = await client.get(f"{base}/stores/{store_id}/heatmap", params={"window_minutes": window})
        heatmap.raise_for_status()
        out["heatmap"] = heatmap.json()
        anomalies = await client.get(
            f"{base}/stores/{store_id}/anomalies",
            params={"limit": 20, "checkout_zone_id": "checkout", "billing_queue_id": "checkout-1"},
        )
        anomalies.raise_for_status()
        out["anomalies"] = anomalies.json()
        return out


def _write_final_doc(
    cfg: dict[str, Any],
    store_reports: dict[str, dict[str, Any]],
    ingest_results: dict[str, dict[str, int]],
    api_results: dict[str, dict[str, Any]],
    output_paths: dict[str, list[str]],
) -> None:
    generated = datetime.now(timezone.utc).isoformat()
    lines = [
        "# Final Dataset Validation",
        "",
        f"**Generated:** {generated}",
        "",
        "## Dataset used",
        "",
    ]
    for store in cfg["stores"]:
        lines.append(f"### {store['store_id']}")
        lines.append(f"- Layout image: `{store.get('layout_image', 'n/a')}`")
        for v in store["videos"]:
            lines.append(f"- `{v['video_path']}` → `{v['camera_id']}` ({v['role']})")
        lines.append("")

    lines.extend(
        [
            "## Commands executed",
            "",
            "```bash",
            "py -3 scripts/generate_store_layout.py --store store1 --layout-image \"A:\\...\\Store 1 - layout.png\"",
            "py -3 scripts/generate_store_layout.py --store store2 --layout-image \"A:\\...\\store 2 - layout.png\"",
            "py -3 scripts/process_challenge_validation.py",
            "```",
            "",
            "## Output files",
            "",
        ]
    )
    for store_id, paths in output_paths.items():
        lines.append(f"### {store_id}")
        for p in paths:
            lines.append(f"- `{p}`")
        lines.append(f"- `data/events/{store_id}/events.jsonl` (merged)")
        lines.append("")

    lines.extend(["## Ingest results", ""])
    for store_id, stats in ingest_results.items():
        lines.append(
            f"- **{store_id}:** accepted={stats['accepted']}, duplicates={stats['duplicates']}, rejected={stats['rejected']}"
        )

    lines.extend(["", "## API metrics returned", ""])
    for store_id, api in api_results.items():
        m = api.get("metrics", {})
        lines.append(f"### {store_id}")
        lines.append(f"- unique_visitors: {m.get('unique_visitors')}")
        lines.append(f"- visitors_inside: {m.get('visitors_inside')}")
        lines.append(f"- conversion_rate: {m.get('conversion_rate')}")
        lines.append(f"- is_empty: {m.get('is_empty')}")
        funnel = api.get("funnel", {})
        stages = funnel.get("stages") or []
        if stages:
            lines.append(f"- funnel_stages: {len(stages)}")
        heat = api.get("heatmap", {})
        cells = heat.get("cells") or []
        lines.append(f"- heatmap_cells: {len(cells)}")
        anomalies = api.get("anomalies", {})
        items = anomalies.get("items") or anomalies.get("anomalies") or []
        lines.append(f"- anomalies_count: {len(items)}")
        lines.append("")

    lines.extend(["## Reports", f"- `{REPORT_MD.relative_to(ROOT).as_posix()}`", f"- `{REPORT_JSON.relative_to(ROOT).as_posix()}`", ""])
    FINAL_MD.write_text("\n".join(lines), encoding="utf-8")


def _clear_store_events(store_id: str, events_root: Path) -> None:
    target = events_root / store_id
    if target.exists():
        for path in target.rglob("*.jsonl"):
            path.unlink()
    done_dir = target / ".done"
    if done_dir.exists():
        for path in done_dir.glob("*.marker"):
            path.unlink()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Challenge dataset validation")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Delete existing per-store JSONL before processing",
    )
    parser.add_argument(
        "--ingest-only",
        action="store_true",
        help="Skip pipeline; merge, report, ingest, and verify API only",
    )
    parser.add_argument(
        "--only",
        action="append",
        metavar="STORE:CAMERA",
        help="Process only these store:camera pairs (repeatable)",
    )
    args = parser.parse_args()
    only_pairs: set[tuple[str, str]] | None = None
    if args.only:
        only_pairs = set()
        for spec in args.only:
            store_id, _, camera_id = spec.partition(":")
            if not store_id or not camera_id:
                raise ValueError(f"Invalid --only value (expected store:camera): {spec}")
            only_pairs.add((store_id, camera_id))

    cfg = _load_json(CONFIG_PATH)
    events_root = ROOT / cfg.get("events_root", "data/events")
    generated_at = datetime.now(timezone.utc).isoformat()

    if args.fresh:
        for store in cfg["stores"]:
            _clear_store_events(str(store["store_id"]), events_root)

    if not args.ingest_only:
        for store in cfg["stores"]:
            store_id = str(store["store_id"])
            for item in store["videos"]:
                camera_id = str(item["camera_id"])
                if only_pairs is not None and (store_id, camera_id) not in only_pairs:
                    continue
                if _camera_run_complete(store_id, camera_id, events_root):
                    print(f"SKIP (complete): {store_id}/{camera_id}", flush=True)
                    continue
                _reset_incomplete_camera_output(store_id, camera_id, events_root)
                role = str(item.get("role", "zone"))
                layout = _layout_for_role(store, role)
                _run_video(store_id, camera_id, str(item["video_path"]), layout, events_root)
                _mark_camera_done(store_id, camera_id, events_root)

    store_reports: dict[str, dict[str, Any]] = {}
    merged_paths: dict[str, list[str]] = {}
    all_rows_by_store: dict[str, list[dict[str, Any]]] = {}

    for store in cfg["stores"]:
        store_id = str(store["store_id"])
        files = _collect_camera_files(store_id, events_root)
        if not files:
            raise RuntimeError(f"No JSONL files produced for {store_id}")
        rows = _read_events(files)
        if not rows:
            raise RuntimeError(f"Empty events for {store_id}")
        merged = events_root / store_id / "events.jsonl"
        _merge_jsonl(rows, merged)
        store_reports[store_id] = _build_store_report(store_id, rows)
        merged_paths[store_id] = [str(f.relative_to(ROOT)) for f in files]
        all_rows_by_store[store_id] = rows

    REPORT_JSON.write_text(
        json.dumps({"generated_at": generated_at, "stores": store_reports}, indent=2),
        encoding="utf-8",
    )
    REPORT_MD.write_text(_render_markdown(store_reports, generated_at), encoding="utf-8")

    ingest_results: dict[str, dict[str, int]] = {}
    api_results: dict[str, dict[str, Any]] = {}

    async def _post_ingest_and_verify() -> None:
        for store_id, rows in all_rows_by_store.items():
            ingest_results[store_id] = await _ingest_store(rows)
            api_results[store_id] = await _fetch_api_metrics(store_id)

    asyncio.run(_post_ingest_and_verify())
    _write_final_doc(cfg, store_reports, ingest_results, api_results, merged_paths)

    print(f"REPORT_MD={REPORT_MD}")
    print(f"FINAL_MD={FINAL_MD}")
    print(json.dumps(store_reports, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
