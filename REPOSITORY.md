# Repository file reference

Every path below exists to support **modularity**, **contracts-first development**, and **easy debugging** in a 48-hour challenge.

## Root

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Orchestrates Postgres, migrate, API, dashboard; pipeline under `--profile full` |
| `Dockerfile` | Default API image (alias of `docker/Dockerfile.api`) |
| `.env.example` | Documented environment variables; copy to `.env` |
| `.gitignore` | Excludes venv, data artifacts, secrets |
| `Makefile` | Developer shortcuts: test, migrate, compose, replay |
| `pytest.ini` | Test discovery, markers, 70% coverage gate |
| `requirements.txt` | Runtime dependencies |
| `requirements-dev.txt` | pytest, ruff, mypy, testcontainers |
| `pyproject.toml` | Ruff/mypy project metadata |
| `README.md` | Quick start for reviewers |
| `DESIGN.md` | Architecture reference |
| `CHOICES.md` | Decision log |
| `REPOSITORY.md` | This file |

## `docker/`

| File | Purpose |
|------|---------|
| `Dockerfile.api` | API + Alembic image |
| `Dockerfile.pipeline` | CV worker with OpenCV libs |
| `Dockerfile.dashboard` | Lightweight Streamlit image |

## `configs/`

| File | Purpose |
|------|---------|
| `store_layout.yaml` | Zones, lines, queues, staff ROIs — hot-reloadable store geometry |
| `models.yaml` | YOLO/tracker/Re-ID thresholds |
| `logging.yaml` | structlog processor reference |

## `shared/`

| File | Purpose |
|------|---------|
| `logging.py` | One structlog configuration for all services |
| `settings.py` | Base `AppSettings` for pydantic-settings |

## `schemas/` (contracts — no I/O)

| File | Purpose |
|------|---------|
| `events.py` | `EventEnvelope`, `EventType`, deterministic `generate_event_id` |
| `api.py` | REST DTOs: batch ingest, metrics, funnel, heatmap, anomalies, health |
| `config.py` | Validates `store_layout.yaml` |

## `app/` (canonical FastAPI package)

| File | Purpose |
|------|---------|
| `main.py` | Application factory, lifespan, CORS, OpenAPI, router wiring |
| `database.py` | Async engine, retry connect, session DI |
| `models.py` | SQLAlchemy ORM (Alembic target) |
| `dependencies.py` | Settings, DB, services, API key |
| `settings.py` | Environment-based configuration |
| `state.py` | Runtime degradation (`ok` / `degraded` / `down`) |
| `repositories.py` | Persistence layer |
| `services/` | Ingestion, metrics, analytics use-cases |
| `routers/` | `/health`, `/v1/events`, `/v1/metrics`, `/v1/analytics` |

## `api/` (legacy — re-exports `app`)

| File | Purpose |
|------|---------|
| `main.py` | Re-exports `app.main` for backward compatibility |
| `settings.py` | DB URL, API key, CORS |
| `dependencies.py` | Re-exports `app.dependencies` |
| **domain/** | Pure business rules |
| `domain/ingestion.py` | Accept/duplicate/reject partitioning |
| `domain/funnel.py` | Funnel response builder |
| `domain/heatmap.py` | Cell binning helpers |
| `domain/metrics.py` | Real-time metric calculations |
| `domain/anomalies.py` | Z-score anomaly rules |
| **adapters/db/** | Persistence |
| `models.py` | SQLAlchemy ORM tables |
| `repositories.py` | Queries and inserts (stubs) |
| `session.py` | Async engine + session factory |
| **adapters/http/routes/** | HTTP surface |
| `health.py` | `/health` |
| `events.py` | `POST /v1/events/batch` |
| `metrics.py` | `GET /v1/metrics/realtime` |
| `analytics.py` | Funnel, heatmap, queue, anomalies |
| **services/** | Use-cases |
| `ingestion_service.py` | Batch ingest orchestration |
| `metrics_service.py` | Realtime metrics query |
| `analytics_service.py` | Analytics query orchestration |

## `pipeline/` (Detection — no database)

| File | Purpose |
|------|---------|
| `detect.py` | YOLOv8n person detection CLI, `PersonDetector`, `DetectionRunner` |
| `tracker.py` | ByteTrack via supervision, `TrackingRunner`, visualization |
| `track_state.py` | Centroids, history store, group entry, LOST/ENDED lifecycle |
| `entry_exit.py` | Line crossing, ENTRY/EXIT events, cooldown, group crossings, viz |
| `zones.py` | Shapely zone engine, ZONE_ENTER/EXIT/DWELL, dwell intervals, occlusion |
| `session.py` | UUID visitor sessions, zone/dwell aggregation, serialization |
| `reid.py` | Lightweight histogram/cosine Re-ID, REENTRY events |
| `schemas.py` | Event validation, serialization, metadata, dedup helpers |
| `emit.py` | Append-only JSONL emitter, batching, replay CLI |
| `REID.md` | Re-ID limitations, edge cases, scaling |
| `config.py` | `DetectionConfig` — thresholds, paths, YAML merge |
| `utils.py` | `VideoReader`, visualization, batch path resolution |
| `main.py` | CLI dispatch: `detect` vs full `run` |
| `settings.py` | Video source, paths, feature flags |
| `app/interfaces.py` | ABCs: detector, tracker, reid, emitter, frame source |
| `app/runner.py` | Frame loop wiring (stub loop) |
| `app/detector.py` | YOLOv8 wrapper stub |
| `app/tracker.py` | ByteTrack wrapper stub |
| `app/reid.py` | Re-ID gallery stub |
| `app/geometry.py` | Point-in-polygon, line crossing |
| `app/entry_exit.py` | Entry/exit event builder |
| `app/zones.py` | Zone FSM stub |
| `app/dwell.py` | Dwell timer stub |
| `app/queue.py` | Queue depth stub |
| `app/staff.py` | Staff zone classifier |
| `app/reentry.py` | Re-entry window stub |
| `app/calibration.py` | Confidence calibration |
| `app/emitter.py` | JSONL writer |
| `app/frame_source.py` | VideoCapture abstraction |

## `ingest/`

| File | Purpose |
|------|---------|
| `client.py` | HTTP batch client with tenacity retries |
| `replay.py` | CLI to replay JSONL → API |

## `dashboard/`

| File | Purpose |
|------|---------|
| `app.py` | Streamlit entry |
| `api_client.py` | Typed API consumer |
| `settings.py` | API URL, refresh interval |
| `components/kpi_cards.py` | Visitor/queue KPI UI |
| `components/anomaly_feed.py` | Anomaly list UI |

## `alembic/`

| File | Purpose |
|------|---------|
| `env.py` | Async migration runner |
| `versions/001_initial_schema.py` | Initial DDL |

## `scripts/`

| File | Purpose |
|------|---------|
| `seed_demo_data.py` | Dev seed placeholder |
| `run_pipeline.sh` | Local pipeline launcher |
| `replay_events.sh` | Replay wrapper |

## `tests/`

| File | Purpose |
|------|---------|
| `conftest.py` | Shared fixtures |
| `test_schemas_events.py` | Event contract tests |
| `test_domain_*.py` | Domain unit tests |
| `integration/test_health.py` | FastAPI TestClient smoke |
| `fixtures/sample_events.jsonl` | Contract golden file |

## `data/`

| Path | Purpose |
|------|---------|
| `sample/` | Place demo video here |
| `events/` | Pipeline JSONL output |
