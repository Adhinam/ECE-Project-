# Store Intelligence — Submission Guide

Retail analytics from CCTV: YOLOv8 detection, ByteTrack tracking, structured challenge events, PostgreSQL API, Streamlit dashboard.

## Prerequisites

- Docker Desktop (recommended) **or** Python 3.11+ with local Postgres
- Challenge dataset videos on disk (Store 1 / Store 2 ZIPs)
- ~8 GB disk for models + event output
- CPU sufficient for inference (GPU optional)

## Installation

### Option A — Docker (recommended)

```bash
git clone <repo-url> store-intelligence
cd store-intelligence
make env          # creates .env from .env.example
make up-detach    # postgres → migrate → api → dashboard
make health
```

| Service | URL |
|---------|-----|
| API docs | http://localhost:8000/docs |
| Health | http://localhost:8000/health |
| Dashboard | http://localhost:8501 |

### Option B — Local Python

```bash
make dev-install
cp .env.example .env
# Start Postgres locally; set DATABASE_URL in .env
make migrate
make dev-api      # :8000
make dev-dashboard  # :8501
```

### Dependencies

- `requirements.txt` — runtime (FastAPI, Ultralytics, asyncpg, etc.)
- `requirements-dev.txt` — pytest, ruff, mypy
- YOLO weights: `yolov8n.pt` (auto-downloaded or place in repo root)

## Environment setup

Key variables (see `.env.example`):

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Postgres connection |
| `PIPELINE_VIDEO_SOURCE` | Input video path |
| `PIPELINE_STORE_ID` | Store identifier (`store1`, `store2`) |
| `PIPELINE_CAMERA_ID` | Camera id per run |
| `STORE_LAYOUT_PATH` | `configs/store_layout_store1.json` etc. |
| `EVENTS_OUTPUT_DIR` | Default `data/events` |
| `EMIT_CHALLENGE_FORMAT` | `true` for challenge JSONL |
| `ENABLE_REID` | `true` for re-entry detection |
| `MODEL_PATH` | Path to `yolov8n.pt` (leave empty to use YAML default) |

**Important:** Empty `MODEL_PATH=` in `.env` is treated as unset (uses `configs/models.yaml` default).

## Startup commands

```bash
# Full stack
make up-detach && make health

# API only (local)
make dev-api

# Dashboard only
make dev-dashboard
```

## Inference commands

### Generate store layout from layout PNG

```bash
py -3 scripts/generate_store_layout.py --store store1 --layout-image "A:\...\Store 1 - layout.png"
py -3 scripts/generate_store_layout.py --store store2 --layout-image "A:\...\store 2 - layout.png"
```

### Single camera (full pipeline)

```bash
set PIPELINE_STORE_ID=store1
set PIPELINE_CAMERA_ID=cam-entry
set PIPELINE_VIDEO_SOURCE=A:\Store 1-...\CAM 3 - entry.mp4
set STORE_LAYOUT_PATH=configs\store_layout_store1.json
set EVENTS_OUTPUT_DIR=data\events
set EMIT_CHALLENGE_FORMAT=true
set ENABLE_REID=true
py -3 -m pipeline.main run
```

### Batch — all challenge cameras

```bash
py -3 scripts/process_challenge_validation.py
```

Camera-to-role mapping: `configs/challenge_batch_config.json`

| Store | Camera | Role | Layout |
|-------|--------|------|--------|
| store1 | cam-zone-1, cam-zone-2 | zone | `store_layout_store1_zone.json` |
| store1 | cam-entry | entry | `store_layout_store1.json` |
| store1 | cam-billing | billing | `store_layout_store1.json` |
| store2 | cam-entry-1, cam-entry-2 | entry | `store_layout_store2.json` |
| store2 | cam-zone | zone | `store_layout_store2_zone.json` |
| store2 | cam-billing | billing | `store_layout_store2.json` |

## API usage

### Ingest events

```bash
curl -s -X POST http://localhost:8000/events/ingest \
  -H "Content-Type: application/json" \
  -d '{"events":[{"event_id":"00000000-0000-4000-8000-000000000001","store_id":"store1","camera_id":"cam-entry","visitor_id":"VIS_a1b2c3","event_type":"ENTRY","timestamp":"2026-06-02T17:00:00Z","confidence":0.9}]}'
```

Batch replay:

```bash
py -3 scripts/process_challenge_validation.py --ingest-only
# or
python -m ingest.replay --input data/events/store1/events.jsonl --api-url http://localhost:8000
```

### Analytics endpoints

Use `window_minutes=1440` for batch-processed historical events:

```bash
curl -s "http://localhost:8000/stores/store1/metrics?window_minutes=1440"
curl -s "http://localhost:8000/stores/store1/funnel?window_minutes=1440&checkout_zone_id=checkout&billing_queue_id=checkout-1"
curl -s "http://localhost:8000/stores/store1/heatmap?window_minutes=1440&resolution=32"
curl -s "http://localhost:8000/stores/store1/anomalies?limit=10&checkout_zone_id=checkout&billing_queue_id=checkout-1"
```

Legacy prefixed routes also available: `/v1/events/ingest`, `/v1/metrics/realtime`.

## Expected outputs

After processing both stores:

```
data/events/
  store1/
    events.jsonl              # merged (945 events)
    2026-06-02/
      cam-zone-1.jsonl
      cam-zone-2.jsonl
      cam-entry.jsonl
      cam-billing.jsonl
  store2/
    events.jsonl              # merged (880 events)
    2026-06-02/
      cam-entry-1.jsonl
      cam-entry-2.jsonl
      cam-zone.jsonl
      cam-billing.jsonl
  validation_report.md
  validation_report.json
```

Each JSONL line is challenge format:

```json
{
  "event_id": "...",
  "store_id": "store1",
  "camera_id": "cam-entry",
  "visitor_id": "VIS_abc123",
  "event_type": "ZONE_ENTER",
  "timestamp": "2026-06-02T17:39:14Z",
  "zone_id": "main-floor",
  "dwell_ms": 0,
  "is_staff": false,
  "confidence": 0.85,
  "metadata": {"queue_depth": null, "sku_zone": "main-floor", "session_seq": 1}
}
```

### Regenerate reports from ingested DB (no re-inference)

```bash
py -3 scripts/export_events_from_db.py --dsn postgresql://store_intel:store_intel_dev@localhost:5432/store_intel
py -3 scripts/regenerate_validation_reports.py
```

If local Postgres auth fails, export via Docker psql + `scripts/convert_raw_export.py` (see `FINAL_DATASET_VALIDATION.md`).

## Validation

```bash
py -3 scripts/regenerate_validation_reports.py
# Reports written to data/events/validation_report.{md,json}
```

## Further reading

- [FINAL_DATASET_VALIDATION.md](FINAL_DATASET_VALIDATION.md) — challenge dataset results
- [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) — submission checklist
- [LIMITATIONS.md](LIMITATIONS.md) — known gaps
- [README.md](README.md) — architecture overview
