"""Regenerate validation reports from merged events.jsonl files (no inference)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS_ROOT = ROOT / "data" / "events"
REPORT_JSON = EVENTS_ROOT / "validation_report.json"
REPORT_MD = EVENTS_ROOT / "validation_report.md"

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
CORE_EVENT_TYPES = REQUIRED_EVENT_TYPES - {"REENTRY"}


def _read_events(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _event_type_counts(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        et = str(row.get("event_type", ""))
        counts[et] = counts.get(et, 0) + 1
    return counts


def _per_camera_counts(rows: list[dict]) -> dict[str, dict[str, int]]:
    by_cam: dict[str, dict[str, int]] = {}
    for row in rows:
        cam = str(row.get("camera_id", "unknown"))
        et = str(row.get("event_type", ""))
        by_cam.setdefault(cam, {})
        by_cam[cam][et] = by_cam[cam].get(et, 0) + 1
    return by_cam


def _zone_dwell_stats(rows: list[dict]) -> dict[str, dict[str, float]]:
    by_zone: dict[str, list[float]] = {}
    for row in rows:
        if str(row.get("event_type")) != "ZONE_DWELL":
            continue
        zone_id = str(row.get("zone_id") or "UNKNOWN")
        by_zone.setdefault(zone_id, []).append(float(row.get("dwell_ms") or 0.0))
    out: dict[str, dict[str, float]] = {}
    for zone_id, values in by_zone.items():
        out[zone_id] = {
            "count": float(len(values)),
            "avg_dwell_ms": float(sum(values) / len(values)),
            "max_dwell_ms": float(max(values)),
            "min_dwell_ms": float(min(values)),
        }
    return out


def _build_store_report(store_id: str, rows: list[dict], merged_path: Path) -> dict:
    counts = _event_type_counts(rows)
    visitors = {str(r.get("visitor_id")) for r in rows if r.get("visitor_id")}
    present = set(counts.keys())
    missing_core = sorted(CORE_EVENT_TYPES - present)
    reentry_count = counts.get("REENTRY", 0)
    reentry_status = "present" if reentry_count else "not_observed_in_dataset"
    return {
        "store_id": store_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "merged_events_path": str(merged_path.relative_to(ROOT)),
        "total_events": len(rows),
        "total_visitors": len(visitors),
        "total_entries": counts.get("ENTRY", 0),
        "total_exits": counts.get("EXIT", 0),
        "total_reentries": reentry_count,
        "reentry_status": reentry_status,
        "queue_join": counts.get("BILLING_QUEUE_JOIN", 0),
        "queue_abandon": counts.get("BILLING_QUEUE_ABANDON", 0),
        "validation": {
            "event_types_present": sorted(present),
            "required_core_types_missing": missing_core,
            "all_core_types_present": len(missing_core) == 0,
            "reentry_status": reentry_status,
        },
        "event_type_counts": counts,
        "zone_dwell_statistics": _zone_dwell_stats(rows),
        "per_camera_event_counts": _per_camera_counts(rows),
    }


def _render_md(reports: list[dict], generated_at: str) -> str:
    lines = ["# Validation Report", "", f"Generated: {generated_at}", ""]
    for rep in reports:
        val = rep["validation"]
        lines.extend(
            [
                f"## {rep['store_id']}",
                "",
                f"- **Total events:** {rep['total_events']}",
                f"- **Total visitors:** {rep['total_visitors']}",
                f"- **Entries:** {rep['total_entries']}",
                f"- **Exits:** {rep['total_exits']}",
                f"- **Reentries:** {rep['total_reentries']}",
                f"- **Reentry status:** {rep['reentry_status']}",
                f"- **Queue join:** {rep['queue_join']}",
                f"- **Queue abandon:** {rep['queue_abandon']}",
                f"- **All core event types present:** {val['all_core_types_present']}",
            ]
        )
        if val["required_core_types_missing"]:
            lines.append(f"- **Missing core types:** {', '.join(val['required_core_types_missing'])}")
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


def main() -> int:
    generated_at = datetime.now(timezone.utc).isoformat()
    reports: list[dict] = []
    for store_id in ("store1", "store2"):
        merged = EVENTS_ROOT / store_id / "events.jsonl"
        if not merged.is_file():
            raise FileNotFoundError(f"Missing merged events: {merged}")
        rows = _read_events(merged)
        reports.append(_build_store_report(store_id, rows, merged))

    REPORT_JSON.write_text(json.dumps({"generated_at": generated_at, "stores": reports}, indent=2), encoding="utf-8")
    REPORT_MD.write_text(_render_md(reports, generated_at), encoding="utf-8")
    print(f"Wrote {REPORT_JSON}")
    print(f"Wrote {REPORT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
