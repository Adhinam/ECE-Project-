# Store Intelligence Platform

Production-style retail analytics platform built for **Purplle Tech Challenge 2026 – Round 2**.

The system processes CCTV footage, detects and tracks customers, generates structured retail events, computes real-time store analytics, exposes APIs through FastAPI, and visualizes insights through a live Streamlit dashboard.

---

## Features

### Computer Vision Pipeline

* YOLOv8-based person detection
* Multi-object tracking
* Entry / Exit detection
* Zone visit tracking
* Dwell time computation
* Queue monitoring
* Re-entry detection
* Structured challenge-event generation

### Analytics Platform

* Event ingestion API
* Real-time metrics engine
* Conversion funnel analytics
* Heatmap generation
* Queue analytics
* Anomaly detection
* Store health monitoring

### Dashboard

* Live KPI cards
* Visitor analytics
* Conversion funnel visualization
* Heatmap visualization
* Queue monitoring
* Active anomaly feed

---

## Quick Start (Docker)

### 1. Create Environment

```bash
make env
```

### 2. Start Platform

```bash
make up-detach
```

### 3. Verify Health

```bash
make health
```

### 4. Open Services

| Service   | URL                          |
| --------- | ---------------------------- |
| API Docs  | http://localhost:8000/docs   |
| Health    | http://localhost:8000/health |
| Dashboard | http://localhost:8501        |

---

## Local Development

Install dependencies:

```bash
make dev-install
```

Run database migrations:

```bash
make migrate
```

Start API:

```bash
make dev-api
```

Start dashboard:

```bash
make dev-dashboard
```

Run tests:

```bash
make test
```

---

## End-to-End Challenge Workflow

### Start Platform

```bash
docker compose build
docker compose up -d
```

### Run Detection Pipeline

```bash
./pipeline/run.sh
```

Batch process all clips:

```bash
PIPELINE_BATCH_ALL_CLIPS=1 ./pipeline/run.sh
```

### Replay Events

```bash
python -m pipeline.emit replay \
  --input data/events/ \
  --api-url http://localhost:8000
```

### Verify APIs

```bash
curl http://localhost:8000/health

curl http://localhost:8000/stores/store-001/metrics

curl "http://localhost:8000/stores/store-001/funnel?window_minutes=60"

curl "http://localhost:8000/stores/store-001/heatmap?window_minutes=60&resolution=32"

curl "http://localhost:8000/stores/store-001/anomalies?limit=5"
```

### Open Dashboard

```text
http://localhost:8501
```

Dashboard includes:

* Visitor KPIs
* Funnel metrics
* Queue analytics
* Heatmaps
* Anomaly monitoring

---

## Documentation

* **DESIGN.md** — Architecture, data flow, system design, AI-assisted development decisions
* **CHOICES.md** — Model selection, schema design, API decisions, engineering trade-offs
* **LIMITATIONS.md** — Known limitations and future improvements
* **HARDENING.md** — Reliability, scalability, deployment, and production-readiness considerations
* **EVENT_TYPE_RECONCILIATION.md** — Event reconciliation and metric interpretation notes
* **REPOSITORY.md** — Repository structure and file overview
* **SUBMISSION_README.md** — Reviewer guidance and challenge-specific notes

---

## Repository Structure

```text
pipeline/        CCTV processing pipeline
app/             FastAPI application
api/             Domain-oriented API layer
dashboard/       Streamlit dashboard
schemas/         Shared event schemas
scripts/         Utility scripts
configs/         Store layouts and configurations
tests/           Automated tests
docs/            Supporting documentation
```

Full repository structure is available in `REPOSITORY.md`.

---

## Testing

Run complete test suite:

```bash
make test
```

Run linting:

```bash
make lint
```

---



## Tech Stack

* Python 3.11+
* FastAPI
* PostgreSQL
* SQLAlchemy
* Streamlit
* Docker
* YOLOv8
* OpenCV
* Pytest

---

## Challenge Submission

**Purplle Tech Challenge 2026 – Round 2**

This repository contains:

* Detection and tracking pipeline
* Event generation system
* Retail analytics APIs
* Dashboard implementation
* Automated tests
* Architecture documentation
