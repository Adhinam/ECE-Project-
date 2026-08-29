# Production Hardening Review

Review date: 2026-05-30. Scope: full repository (`app/`, `api/`, `schemas/`, `ingest/`, `dashboard/`, `pipeline/`, Docker).

This document lists findings, **fixes applied in this pass**, and **TODO markers** for future scaling. Intended audience: senior engineers doing a technical review.

---

## Executive summary

| Area | Before | After this pass |
|------|--------|-----------------|
| Observability | structlog via `PrintLoggerFactory`; no request IDs | stdlib-integrated structlog; `X-Request-ID`; access logs |
| Ingest races | SELECT-then-INSERT could 503 on PK conflict | `ON CONFLICT DO NOTHING` + concurrent duplicate accounting |
| Health watermark | `pipeline_last_event_at` never updated on ingest | `AppState.note_pipeline_events()` on successful ingest |
| Broken code | `pipeline/tests/test_emit_schemas.py` syntax error | Fixed |
| Legacy stub | `api/adapters/.../health.py` returned incomplete `HealthResponse` | Deprecated route; delegates to `HealthChecker` |
| Docker | API image missing `ingest/` | Added for replay CLI in container |

**Test gate:** `pytest tests/ api/tests` with 70% coverage (unchanged).

---

## 1. Broken imports & missing dependencies

### Findings

| Issue | Severity | Status |
|-------|----------|--------|
| `tenacity` required by `ingest/client.py` but not always installed locally | Medium | Already in `requirements.txt`; run `pip install -r requirements.txt` |
| `api/adapters/http/routes/health.py` imported broken `HealthResponse` shape | Low | **Fixed** — deprecated stub |
| `pipeline/tests` not collected in default CI (CV deps) | Low | Documented; syntax error **fixed** |
| Legacy `api/adapters/db/repositories.py` stubs unused by canonical app | Info | TODO: remove or wire in v2 |

### Action

```bash
make dev-install   # requirements + requirements-dev.txt
```

---

## 2. Typing inconsistencies

### Findings

- Duplicate `MAX_INGEST_BATCH_SIZE` in `app/ingestion.py` and `schemas/api.py` — **fixed** (single source: `schemas.api`).
- `get_db` dependency used untyped `Depends(get_db_session)` — **fixed** with `Annotated`.
- `api/domain/*` and `app/*` engines use mixed tuple vs dataclass internals — acceptable; public API is Pydantic.

### TODO

```python
# TODO(typing): enable strict mypy on app/ and schemas/ in CI (Makefile lint target exists)
```

---

## 3. Unhandled exceptions

### Findings

| Location | Risk | Fix |
|----------|------|-----|
| Ingest transaction failure | Logged + 503 | Unchanged (correct) |
| Unhandled 500s | No structured log | **`app/exceptions.py`** global handlers |
| `HTTPException` swallowed by generic handler | Would become 500 | **Fixed** — dedicated `StarletteHTTPException` handler |
| Dashboard `fetch_live_snapshot` | Swallows errors into `errors` dict | OK for UI; errors visible in Streamlit |

---

## 4. Race conditions

### Ingest duplicate `event_id` (concurrent POSTs)

**Before:** Two workers both pass `find_existing_ids`, one INSERT fails → entire batch 503.

**After:** `EventRepository.insert_events` uses PostgreSQL `INSERT ... ON CONFLICT DO NOTHING`. Skipped rows increment `duplicates` in ingest response.

```python
# app/repositories.py — insert_events()
stmt = pg_insert(RawEvent).values(rows).on_conflict_do_nothing(index_elements=[RawEvent.event_id])
```

### Health `AppState` mutation on every `/health` call

`/health` calls `mark_db_up` / `mark_db_down` based on live `SELECT 1`. Under flaky network this toggles readiness. **Accepted** for pilot; for production:

```python
# TODO(reliability): hysteresis — require N consecutive failures before mark_db_down
```

### Streamlit refresh + ThreadPoolExecutor

Dashboard parallel fetches are read-only — no shared mutable state. Safe.

---

## 5. Logging gaps

### Applied

| Component | Change |
|-----------|--------|
| `shared/logging.py` | stdlib + structlog `ProcessorFormatter` (JSON in prod) |
| `app/middleware.py` | `http_request` log with `request_id`, `duration_ms`, `status_code` |
| `app/ingestion.py` | `ingest_concurrent_duplicates` when ON CONFLICT skips rows |
| `app/exceptions.py` | `validation_failed`, `database_error`, `unhandled_exception` with `exc_info` |

### Remaining gaps (TODO)

```python
# TODO(observability): OpenTelemetry traces (FastAPI instrumentation + asyncpg)
# TODO(observability): Prometheus /metrics endpoint (ingest latency histogram, pool checkout time)
# TODO(observability): bind store_id on ingest routes via middleware when path matches /events/*
```

---

## 6. Docker issues

| Issue | Fix |
|-------|-----|
| API image lacked `ingest/` | **Added** to `docker/Dockerfile.api` |
| Compose API healthcheck uses `/health` (DB-dependent); Dockerfile uses `/live` | **OK** — Compose validates full stack; K8s should use `/live` + `/ready` separately |
| `pipeline` profile not in default `up` | Intentional — keeps laptop demo light |
| No non-root user on dashboard/pipeline images | TODO below |

```dockerfile
# TODO(docker): non-root USER on Dockerfile.dashboard and Dockerfile.pipeline
# TODO(docker): multi-stage API image to shrink layer size
```

### Recommended probes (Kubernetes)

| Probe | Path | Expect |
|-------|------|--------|
| Liveness | `/live` | 200 |
| Readiness | `/ready` | 200 + `db_available: true` |
| Startup | `/health` | 200, status not `down` |

---

## 7. SQLAlchemy issues

| Issue | Status |
|-------|--------|
| No `pool_recycle` | **Fixed** — `db_pool_recycle=1800` in settings |
| `get_db_session` does not auto-commit | Correct — ingest uses explicit `begin()` |
| Nested `begin()` on request session | Valid in SQLAlchemy 2.0 |
| On-read metrics = 4–5 queries per request | **TODO** in `StoreMetricsRepository.fetch_all` |
| `AnomalyRepository.insert` commits inside repo | Inconsistent with EventRepository; OK until anomaly persistence is live |

```python
# TODO(scaling): statement_timeout on analytics queries
# TODO(scaling): read replica routing for GET /stores/*/metrics|funnel|anomalies
```

---

## 8. FastAPI lifecycle

| Check | Status |
|-------|--------|
| Engine disposed on shutdown | Yes — `close_database()` in lifespan |
| Degraded start without DB | Yes — `AppState.mark_db_down` |
| Settings cached with `@lru_cache` | Yes — tests call `reset_database_singletons()` |
| Multiple workers + in-memory watermark | **Limitation** — `pipeline_last_event_at` is per-process; health also reads DB timestamps |

```python
# TODO(scaling): Redis for cross-worker pipeline watermark if needed
```

### Middleware order

`RequestContextMiddleware` → `CORSMiddleware` → routes. Request ID available in all route logs.

---

## 9. Performance bottlenecks

| Hot path | Issue | Mitigation now | Future |
|----------|-------|----------------|--------|
| `GET /stores/{id}/metrics` | Loads all session events in window | Window capped at 1440 min | Materialize `metric_buckets` on ingest |
| `GET /stores/{id}/funnel` | Python session reconstruction | Same | Pre-aggregate `funnel_counts` |
| `GET /health` | 5+ aggregate queries | Acceptable at pilot scale | Cache 10–30s or materialized view |
| Ingest | Pre-check `find_existing_ids` + INSERT | ON CONFLICT handles race; pre-check still saves work | Optional: drop pre-check, rely on conflict only |
| Dashboard 2s poll | 3 parallel HTTP calls | Connection pooling in `DashboardApiClient` | SSE or WebSocket |

---

## 10. Cleanup opportunities

| Item | Recommendation |
|------|----------------|
| Duplicate `api/adapters/http/routes/*` | Delete or mark deprecated when v2 API frozen |
| `app/main.py` `app.state.pipeline_last_event_at` | Sync from `runtime` or remove in v2 |
| `MetricRepository` + empty `metric_buckets` | Wire rollup job or document as unused |
| `pipeline/app/*` stubs vs top-level modules | Consolidate when `pipeline.main run` is production-ready |
| Root git repo is `C:/Users/anush` not project | **Ops:** init git inside `store-intelligence/` for real reviews |

---

## 11. Patches applied (file list)

```
app/middleware.py          NEW — request ID + access log
app/exceptions.py          NEW — global exception handlers
shared/logging.py          stdlib structlog integration
app/main.py                wire middleware + handlers
app/state.py               pipeline_last_event_at watermark
app/ingestion.py           watermark, concurrent dupes, import MAX from schemas
app/repositories.py        ON CONFLICT insert, scaling TODO
app/settings.py            db_pool_recycle
app/database.py            pool_recycle passed to engine
app/routers/health.py      read watermark from AppState
app/dependencies.py        Annotated get_db
api/adapters/.../health.py deprecated stub fix
docker/Dockerfile.api      COPY ingest/
pipeline/tests/test_emit_schemas.py  syntax fix
tests/test_repositories_suite.py   mock rowcount
```

---

## 12. Developer experience

| Improvement | Command |
|-------------|---------|
| Run tests with coverage | `make test` |
| Lint | `make lint` |
| Local API | `make dev-api` |
| Replay events | `make replay` |
| Full stack | `make up-detach && make health` |

Add to onboarding: read `DESIGN.md`, `CHOICES.md`, this file.

---

## 13. Sign-off checklist (for reviewers)

- [ ] `pytest tests/ api/tests` passes locally
- [ ] `docker compose up --build` brings API + dashboard + postgres
- [ ] `POST /events/ingest` returns partial success on mixed valid/invalid batch
- [ ] Replay same JSONL twice → second run `duplicates` > 0, no 503
- [ ] `/health` returns `checks` with database + health_queries
- [ ] Response includes `X-Request-ID` header
- [ ] Logs are JSON when `LOG_JSON=true`

---

## 14. Priority backlog (post-review)

1. **P0** — Materialized metrics rollups when event volume > ~100k rows/store/day  
2. **P1** — OpenTelemetry + `/metrics`  
3. **P1** — DB health hysteresis for readiness  
4. **P2** — Remove legacy `api/adapters` HTTP routes  
5. **P2** — Pipeline test job in CI (optional profile with CV deps)  
6. **P3** — Git repository scoped to project directory only  
