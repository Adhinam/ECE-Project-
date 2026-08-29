# Design — Store Intelligence Platform

This document describes how the system is built today: what runs where, how data moves, and which tradeoffs we accepted on purpose. It is written for engineers onboarding to the repo or reviewing the architecture before extending it.

For a file-by-file map, see [REPOSITORY.md](REPOSITORY.md). For the decision log in table form, see [CHOICES.md](CHOICES.md).

---

## 1. Architecture overview

The platform turns CCTV into structured retail events, persists them in PostgreSQL, and serves analytics over HTTP to operators and a live dashboard. Three concerns stay separated:

| Concern | Package | Owns |
|---------|---------|------|
| **Contracts** | `schemas/` | Pydantic models only — no I/O, no framework imports |
| **Edge / CV** | `pipeline/` | Frames → detections → tracks → domain events → JSONL |
| **Serving** | `app/` | FastAPI, ingestion, metrics/funnel/anomalies, health |
| **Presentation** | `dashboard/` | Streamlit UI polling the API |
| **Transport** | `ingest/` | HTTP client and JSONL replay toward the API |

The `api/` tree is a **compatibility shim**: `api/main.py` re-exports `app.main`, and `api/domain/` holds pure helpers that mirror business rules. New work should land in `app/` first.

## Architecture Overview

```text
Video / RTSP / Files
        |
        v
YOLOv8 Detection
        |
        v
ByteTrack Tracking
        |
        v
Geometry Processing
        |
        v
Session + Re-ID
        |
        v
JSONL Events
        |
        +------------------+
        |                  |
        v                  v
   Replay Client      Event Ingestion API
                             |
                             v
                        PostgreSQL
                             |
                             v
                Metrics / Funnel / Anomalies
                             |
                             v
                   Streamlit Dashboard
```

**Runtime topology (Docker Compose):** `postgres` → one-shot `migrate` (Alembic) → `api` → `dashboard`. The CV worker (`pipeline` service) is behind the `full` profile so reviewers can run API + DB without pulling GPU/OpenCV images.

**Degradation model:** `AppState` tracks whether Postgres is reachable. If not, ingestion returns 503 and read paths that need the DB either fail clearly or return empty structures with `is_empty` / degraded flags — we do not invent synthetic metrics when the database is down.

---

## 2. Data flow

### 2.1 Happy path

1. **Capture** — `pipeline/utils.VideoReader` reads a file or directory of clips with drop-frame tolerance and optional FPS throttling.
2. **Detect** — `PersonDetector` (Ultralytics YOLOv8n, person class) produces bounding boxes per frame.
3. **Track** — `ByteTrackVisitorTracker` assigns stable `track_id` values and maintains trajectory history for visualization and geometry.
4. **Semantics** — Modular runners attach meaning:
   - `entry_exit.py` — line crossings → `ENTRY` / `EXIT`
   - `zones.py` — polygons → `ZONE_ENTER` / `ZONE_EXIT` / `ZONE_DWELL`
   - `session.py` — UUID `visitor_id`, zone visit aggregation, timeouts
   - `reid.py` (optional) — appearance match → `REENTRY` linking prior exit
5. **Emit** — `pipeline/emit.py` appends validated `EventEnvelope` lines to JSONL under `data/events/{store_id}/{date}/`. In-process dedup prevents double-writes during a single run; **API ingest is authoritative across runs**.
6. **Ingest** — `POST /events/ingest` (and alias `POST /v1/events/batch`) validates each object, deduplicates by `event_id`, inserts valid rows in one transaction, records `ingest_batches`.
7. **Query** — Engines read `raw_events` for a store/time window and compute KPIs on demand (metrics, funnel, anomalies). Rollup tables (`metric_buckets`, `funnel_counts`, etc.) exist in schema for future materialization; **current code paths aggregate from raw events** for correctness and simpler debugging.
8. **Dashboard** — `dashboard/api_client.py` fetches metrics, funnel, anomalies, and health in parallel every ~2s.

### 2.2 Event contract

All producers and consumers share `schemas/events.py`:

- **`event_id`** — UUID; pipeline can use deterministic `generate_event_id(...)` for idempotent replay.
- **`occurred_at`** — business time (frame/time in store), not ingest time.
- **`payload`** — extensible JSON (zone IDs, queue IDs, visitor IDs, similarity scores).
- **`is_staff`** — set when track intersects staff ROI; excluded from visitor KPIs.

JSONL is append-only. Replaying the same file twice should yield `duplicates` in the ingest response, not double-counted visitors.

### 2.3 Time semantics

| Timestamp | Meaning |
|-----------|---------|
| `occurred_at` | When the event happened in the store (from video clock or wall clock at edge) |
| `ingested_at` | When the API persisted the row (used for lag and health) |
| `received_at` on `ingest_batches` | Batch receipt audit |

Health and stale-feed logic compare **now** against latest `occurred_at` (pipeline activity) and latest `ingested_at` (ingestion lag).

---

## 3. Detection pipeline

The pipeline is deliberately **staged as CLIs**, not one opaque monolith. That matches how CV work is debugged: isolate detection, then tracking, then geometry, then session logic.

```
detect.py → tracker.py → entry_exit.py / zones.py → session.py → reid.py → emit.py
```

| Stage | Module | Output events |
|-------|--------|----------------|
| Detection | `detect.py` | (internal detections only) |
| Tracking | `tracker.py` | `track_id`, trajectories |
| Entry/exit | `entry_exit.py` | `ENTRY`, `EXIT` (+ group crossing metadata) |
| Zones | `zones.py` | `ZONE_*`, dwell intervals |
| Session | `session.py` | visitor UUID lifecycle, serialized session state |
| Re-ID | `reid.py` | `REENTRY` (optional, `REID_ENABLED`) |
| Emit | `emit.py` | JSONL + `replay` / `validate` subcommands |

`pipeline/main.py run` and `pipeline/app/runner.py` wire a full loop but remain **work in progress** — production demos typically run stage CLIs or replay pre-built JSONL. `make up-full` starts the pipeline container profile when you need the worker in Compose.

**Configuration** lives in `configs/store_layout.yaml` (lines, zones, queues, staff ROIs, re-entry window) and `configs/models.yaml` (confidence, tracker, Re-ID thresholds). Layout is hot-reload friendly at the file level; the API does not parse layout — only events matter downstream.

**Staff handling** is zone-based: tracks in staff ROIs get `is_staff=true`. We avoided a separate classifier model to keep explanations simple (“this polygon is back-of-house”).

**Re-ID** is documented in `pipeline/REID.md`: HSV histogram + cosine on crops, gallery with TTL. It links visitor sessions across exit/re-entry; it does not replace ByteTrack within a clip. See [Edge-case handling](#6-edge-case-handling).

---

## 4. API architecture

### 4.1 Layering

```
HTTP (routers/) → services/ → engines (metrics.py, funnel.py, anomalies.py, ingestion.py) → repositories.py → SQLAlchemy models
```

- **Routers** — Thin: parse query params, call service, return Pydantic response models from `schemas/api.py`.
- **Services** — Construct engines with `AsyncSession` + `AppState`; map domain errors to HTTP.
- **Engines** — Stateful-less aggregation over event rows (session reconstruction, funnel stages, anomaly rules).
- **Repositories** — SQL only; no business rules.
- **Domain (`api/domain/`)** — Pure functions for partitioning ingest results, building funnel DTOs, z-score helpers — usable from tests without FastAPI.

Dependency injection is in `app/dependencies.py`: settings singleton, per-request DB session, optional API key on ingest.

### 4.2 Primary endpoints

| Method | Path | Role |
|--------|------|------|
| `POST` | `/events/ingest`, `/v1/events/batch` | Batch ingest (max 500 events) |
| `GET` | `/stores/{id}/metrics` | Session-aware KPIs (visitors, conversion, queue, dwell) |
| `GET` | `/stores/{id}/funnel` | ENTRY → ZONE_VISIT → BILLING_QUEUE → PURCHASE |
| `GET` | `/stores/{id}/anomalies` | Queue spike, conversion drop, dead zone, stale feed |
| `GET` | `/health` | DB, ingestion lag, per-store and per-camera freshness |
| `GET` | `/live`, `/ready` | K8s-style probes |
| `GET` | `/v1/analytics/*` | Funnel, heatmap, queue aliases for legacy clients |

OpenAPI is enabled at `/docs`. CORS allows the dashboard origin by default.

### 4.3 Ingestion semantics

`EventIngestor` (`app/ingestion.py`) implements **partial success at validation, atomic persistence**:

1. Per-index validation errors → counted as `rejected`, never touch the DB.
2. In-batch duplicate `event_id` → `duplicates`, not inserted.
3. DB-existing `event_id` → `duplicates`.
4. Remaining valid events → single transaction: `insert_events` + `record_ingest_batch`.

If the transaction fails, the client gets 503 and nothing partial is committed. Malformed JSON in one array element does not poison valid siblings.

### 4.4 Analytics engines (summary)

**Metrics** (`app/metrics.py`) — Rebuilds visitor sessions from `ENTRY`/`EXIT`/`ZONE_*`/`QUEUE_*` in a rolling window. Conversion uses a configurable checkout zone as purchase proxy. Returns confidence bands when sample sizes are low.

**Funnel** (`app/funnel.py`) — Maps events to four stages; merges re-entries within `reentry_window_seconds` into one session; counts staff separately.

**Anomalies** (`app/anomalies.py`) — Rule-based detectors with rolling baselines (mean/std or percent drop), severities INFO/WARN/CRITICAL, and human-readable `suggested_action`. Types: `queue_spike`, `conversion_drop`, `dead_zone`, `stale_feed`. Thresholds via `ANOMALY_*` env vars.

**Health** (`app/health.py`) — Aggregates global and per-store timestamps, camera last-seen, ingest batch rate; surfaces `degraded_features` from `AppState` when optional pipeline features were disabled at startup.

---

## 5. Scaling discussion

This codebase targets a **single-store or few-store pilot**, not multi-tenant SaaS at millions of events per second. Still, the boundaries are chosen so growth paths are obvious.

### 5.1 What scales horizontally today

- **API replicas** — Stateless except `AppState` (DB flag, optional pipeline watermark). Put behind a load balancer; use the same `DATABASE_URL`.
- **Ingest** — Batches up to 500 events; idempotent `event_id` lets edge workers retry safely. Multiple pipeline instances per camera should partition by `camera_id` or use separate JSONL paths to avoid write races on one file.
- **Dashboard** — Embarrassingly parallel; each instance polls the API. Not suitable for thousands of users — replace with a proper SPA if needed.

### 5.2 What does not scale yet (and what we’d do)

| Bottleneck | Today | Likely next step |
|------------|-------|------------------|
| Raw-event aggregation | On-read SQL over `raw_events` | Materialize into `metric_buckets` / `funnel_counts` on ingest or via scheduled job |
| JSONL at edge | Local disk | S3-compatible object store + replay worker |
| Single Postgres | One instance | Read replica for analytics; writer for ingest |
| CV throughput | Sequential video per process | One worker per stream; GPU node pool |
| Re-ID gallery | In-process memory | Per-store embedding service with TTL (if accuracy requirements justify it) |

We **deferred Kafka/Redis streams** (see CHOICES.md). For pilot volume, HTTP batch + JSONL replay is easier to operate and demo. When ingest exceeds ~few hundred events/sec sustained, introduce an outbox or stream between edge and API.

### 5.3 Database

Indexes: `(store_id, occurred_at)` on `raw_events`. Time-range queries for metrics/funnel should stay bounded (`window_minutes`, explicit `from`/`to`). Long retention belongs in partitioning or archive tables, not wider indexes on JSONB payload.

---

## 6. Edge-case handling

| Scenario | Behavior |
|----------|----------|
| **Re-entry within window** | Funnel merges sessions; `REENTRY` events carry `visitor_id` / `prior_visitor_id` in payload. Outside window → new visitor. |
| **Staff in ROI** | `is_staff=true`; excluded from visitor counts and funnel numerators. |
| **Empty store / window** | Metrics and funnel return zeros with `is_empty=true`, not errors. |
| **Stale camera feed** | No recent `occurred_at` for store/camera → health `degraded`/`down`, anomaly `stale_feed`. |
| **Ingestion lag** | `ingested_at - occurred_at` beyond thresholds → health ingestion status degraded. |
| **DB unavailable at startup** | `AppState.mark_db_down`; ingest 503; health `down`. |
| **Duplicate replay** | Same `event_id` → `duplicates`, idempotent counts. |
| **Invalid event in batch** | Index reported in `validation_errors`; valid events still insert. |
| **Low sample metrics** | `MetricConfidence` lowered; anomalies require `min_baseline_samples`. |
| **Group entry** | Entry/exit payload may include `group_crossing_id`; funnel still session-based per visitor key. |
| **Track loss / occlusion** | Zone engine uses dwell timers and occlusion rules; tracks may END without EXIT — session timeout closes visit. |
| **Re-ID off or failed crop** | New UUID per entry; no `REENTRY`; system remains consistent. |
| **Conversion without purchase signal** | Checkout zone visit proxies PURCHASE stage; configurable `checkout_zone_id`. |

Tests under `tests/` focus on these paths (ingestion suite, re-entry, stale feed, anomaly detectors) with factories and in-memory repositories so Postgres is not required for CI.

---

## 7. Production considerations

### 7.1 Deployment

- **Images:** `docker/Dockerfile.api`, `Dockerfile.dashboard`, `Dockerfile.pipeline`.
- **Compose:** Healthchecks on API (`/health`) and dashboard (`/_stcore/health`); migrate job gates API start.
- **Secrets:** `API_KEY` optional on ingest; `DATABASE_URL` from env — never commit `.env`.
- **Logs:** structlog JSON (`LOG_JSON=true`) for aggregation in Loki/CloudWatch.

### 7.2 Observability

- Ingest logs batch metrics (accepted/rejected/duplicates/latency_ms).
- Health returns structured `checks[]` and per-store `StoreHealth` with stale minutes.
- OpenAPI documents all public query parameters (windows, zone IDs).

### 7.3 Configuration

Thresholds are environment-driven, not hard-coded:

- `HEALTH_*` — stale feed and ingestion lag
- `ANOMALY_*` — z-scores, conversion drop %, dead zone sensitivity
- `APP_ENV`, `CORS_ORIGINS`, `DATABASE_URL`

Store geometry remains in YAML under `configs/`, versioned with the pipeline deployment.

### 7.4 Testing and quality gate

- `pytest` with 70% coverage on `app`, `api`, `schemas`, `ingest`.
- Isolated DB tests via `tests/db/memory_repository.py` and mocked `AsyncSession` for repositories.
- `pipeline/tests` excluded from default CI until CV deps and a syntax fix in `test_emit_schemas.py` — run locally with full `requirements-dev.txt`.

### 7.5 Known gaps (honest)

- Unified `pipeline.main run` orchestrator is not the primary operational path.
- Rollup tables are schema-ready but not populated on ingest.
- `api/adapters/` HTTP routes are stubs; canonical routes live under `app/routers/`.
- Heatmap endpoint exists; cell binning is lighter than metrics/funnel.

---

## 8. AI-Assisted Decisions

This project was built with heavy use of AI coding assistants (Cursor). That sped up scaffolding but required the same discipline as any fast prototype: **review everything, reject cleverness that does not earn its complexity, and keep contracts stable.**

### 8.1 Where AI helped

- **Boilerplate velocity** — FastAPI lifespan, Alembic schema, Pydantic DTOs, Docker Compose healthchecks, and pytest fixtures appeared quickly so we could focus on retail semantics (funnel stages, re-entry windows, ingest idempotency).
- **Exhaustive edge-case tests** — AI-generated suites for ingestion partial success, stale feeds, funnel re-entry merge, and anomaly thresholds; we kept tests that reflected real invariants and deleted flaky or duplicate cases.
- **Documentation headers** — Test files include `# PROMPT:` / `# CHANGES MADE:` blocks for auditability of what was machine-suggested vs hand-edited.
- **Modular pipeline CLIs** — Stubs and runners in `pipeline/app/` gave a target architecture (interfaces for detector, tracker, emitter) even where implementation stayed in top-level modules.

### 8.2 Where AI suggestions were rejected

- **Message bus first** — Suggestions to add Kafka or Redis Streams up front were declined. Pilot traffic fits HTTP batches + JSONL replay; operational surface area matters more than theoretical throughput.
- **Heavy Re-ID by default** — OSNet / embedding DB proposals were trimmed to optional histogram Re-ID with explicit limitations in `REID.md`. Default path works without GPU embeddings.
- **Separate staff classifier** — Replaced with staff zones in layout YAML — explainable and good enough for KPI exclusion.
- **Dual API implementations** — AI sometimes duplicated logic in `api/adapters/` and `app/`. We standardized on **`app/` as canonical**; `api/` re-exports and pure `domain/` helpers only.
- **Over-abstracted repository hierarchies** — Rejected extra base classes and generic “UnitOfWork” layers; one `repositories.py` per bounded context is enough at this size.
- **Real-time rollup on every ingest** — Deferred materialized aggregates; on-read aggregation is slower but **easier to reason about** while event semantics were still changing.
- **Including pipeline tests in default CI** — Collection fails without OpenCV/NumPy and has a known syntax error in one file; default `pytest.ini` scopes to `tests/` and `api/tests/` until the pipeline test job is its own optional workflow.

### 8.3 Tradeoffs we made

| Choice | Benefit | Cost |
|--------|---------|------|
| On-read analytics | Correct after any replay; simpler code | SQL load grows with event volume |
| JSONL + HTTP ingest | Demo-friendly, no broker ops | Not ideal for >~100–500 evt/s per store without a queue |
| Streamlit dashboard | Live KPIs in hours | Not a multi-user product UI |
| Session reconstruction in Python | Flexible rules (re-entry, staff) | CPU per request; must cap windows |
| Deterministic `event_id` in pipeline | Idempotent replay | Must not reuse IDs for distinct physical events |
| `AppState` degradation flags | Clear 503/health story | Not a full feature-flag service |

### 8.4 Why simplicity over over-engineering

Retail analytics failures are usually **wrong definitions**, not slow loops: counting a re-entry as two visitors, treating staff as shoppers, or reporting “healthy” when a camera stopped an hour ago. We invested in **explicit event types, ingest idempotency, and testable funnel/anomaly rules** instead of microservices.

AI tends to propose another abstraction layer whenever a file grows. Here, growth went into **engines with readable dataclasses** (`_FunnelSession`, `SessionMetrics`) and **configurable thresholds**, not new indirection. When the product needs Kafka or pre-aggregated buckets, those plug in at the ingest boundary and repository layer without rewriting funnel math.

**Rule we followed:** if a senior engineer cannot explain a module in two minutes on a whiteboard, it does not ship — regardless of how polished the generated code looks.

---

## 9. Related documents

| Document | Contents |
|----------|----------|
| [CHOICES.md](CHOICES.md) | Technology choices and deferred items |
| [REPOSITORY.md](REPOSITORY.md) | File-level map |
| [pipeline/REID.md](pipeline/REID.md) | Re-ID limitations and operations |
| [README.md](README.md) | Quick start and commands |

---

## 10. Engineering Thinking: business-metric reasoning

The challenge north star is **offline conversion rate**:

`converted_visitor_sessions / unique_visitor_sessions`

Most architectural choices were evaluated by how they improve this ratio's correctness:

- **Challenge schema adapter** keeps ingest/storage stable while allowing external evaluators to send/consume the exact PDF JSON shape.
- **Session-aware funnel and metrics** prevent event-level double counting, especially for re-entry and grouped movement.
- **POS correlation by billing-zone + time window** aligns with the official dataset constraints (no customer identity in POS rows).
- **Staff exclusion at event time** ensures all downstream queries (metrics/funnel/anomalies/dashboard) inherit the same definition of customer traffic.
- **Health and stale-feed checks** protect the metric from silent data quality failures (camera stalled, ingest lagging).

Rejected alternatives (event bus first, heavy Re-ID as default, API-side staff relabeling) were dropped because they raised complexity without improving conversion accuracy in the scoring harness.
