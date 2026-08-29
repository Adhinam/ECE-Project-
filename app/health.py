"""Production health checks — database, ingestion lag, stale feeds, per-store status."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import check_db_connection
from app.repositories import HealthDataRepository
from app.state import AppState
from schemas.api import (
    DatabaseHealth,
    DependencyStatus,
    HealthCheck,
    HealthResponse,
    IngestionHealth,
    StoreHealth,
)
from shared.logging import get_logger

logger = get_logger(__name__)


class HealthThresholds(BaseSettings):
    """Configurable health thresholds (env prefix HEALTH_)."""

    model_config = SettingsConfigDict(
        env_prefix="HEALTH_",
        env_file=".env",
        extra="ignore",
    )

    stale_feed_minutes_warn: int = Field(default=10, ge=1)
    stale_feed_minutes_critical: int = Field(default=60, ge=1)
    ingestion_lag_warn_seconds: int = Field(default=120, ge=1)
    ingestion_lag_critical_seconds: int = Field(default=600, ge=1)
    api_version: str = Field(default="0.1.0")


@dataclass
class _StoreSignals:
    store_id: str
    latest_event_at: datetime | None = None
    latest_ingested_at: datetime | None = None
    cameras: dict[str, datetime] = field(default_factory=dict)


def _minutes_since(now: datetime, past: datetime | None) -> float | None:
    if past is None:
        return None
    return max(0.0, (now - past).total_seconds() / 60.0)


def _seconds_since(now: datetime, past: datetime | None) -> float | None:
    if past is None:
        return None
    return max(0.0, (now - past).total_seconds())


def _check_status_from_minutes(
    stale_minutes: float | None,
    *,
    thresholds: HealthThresholds,
) -> tuple[str, bool]:
    """Return (status, is_stale) for feed checks."""
    if stale_minutes is None:
        return "unknown", False
    if stale_minutes >= thresholds.stale_feed_minutes_critical:
        return "down", True
    if stale_minutes >= thresholds.stale_feed_minutes_warn:
        return "degraded", True
    return "ok", False


def _ingestion_status_from_lag(
    lag_seconds: float | None,
    *,
    thresholds: HealthThresholds,
) -> str:
    if lag_seconds is None:
        return "unknown"
    if lag_seconds >= thresholds.ingestion_lag_critical_seconds:
        return "down"
    if lag_seconds >= thresholds.ingestion_lag_warn_seconds:
        return "degraded"
    return "ok"


def build_store_health(
    signals: _StoreSignals,
    *,
    now: datetime,
    thresholds: HealthThresholds,
) -> StoreHealth:
    stale_minutes = _minutes_since(now, signals.latest_event_at)
    feed_status, stale_feed = _check_status_from_minutes(stale_minutes, thresholds=thresholds)

    ingestion_lag = _seconds_since(now, signals.latest_ingested_at)
    ingest_status = _ingestion_status_from_lag(ingestion_lag, thresholds=thresholds)

    stale_cameras: list[str] = []
    for camera_id, last_at in signals.cameras.items():
        cam_stale_min = _minutes_since(now, last_at)
        if cam_stale_min is not None and cam_stale_min >= thresholds.stale_feed_minutes_warn:
            stale_cameras.append(camera_id)

    if feed_status == "down" or ingest_status == "down":
        status = "down"
    elif feed_status == "degraded" or ingest_status == "degraded" or stale_cameras:
        status = "degraded"
    elif feed_status == "unknown" and not signals.cameras:
        status = "unknown"
    else:
        status = "ok"

    parts: list[str] = []
    if stale_feed and stale_minutes is not None:
        parts.append(f"event feed stale {stale_minutes:.0f}m")
    if ingestion_lag is not None and ingest_status != "ok":
        parts.append(f"ingestion lag {ingestion_lag:.0f}s")
    if stale_cameras:
        parts.append(f"{len(stale_cameras)} stale camera(s)")

    return StoreHealth(
        store_id=signals.store_id,
        status=status,
        latest_event_at=signals.latest_event_at,
        latest_ingested_at=signals.latest_ingested_at,
        stale_feed=stale_feed,
        stale_minutes=round(stale_minutes, 1) if stale_minutes is not None else None,
        ingestion_lag_seconds=round(ingestion_lag, 1) if ingestion_lag is not None else None,
        camera_count=len(signals.cameras),
        stale_cameras=stale_cameras,
        message="; ".join(parts) if parts else "operating normally",
    )


def compute_overall_status(
    *,
    db_status: str,
    ingestion_status: str,
    global_stale: bool,
    store_statuses: list[str],
    degraded_features: list[str],
) -> str:
    if db_status == "down":
        return "down"
    if (
        global_stale
        or ingestion_status in ("down", "degraded")
        or any(s in ("down", "degraded") for s in store_statuses)
        or degraded_features
    ):
        return "degraded"
    if db_status == "up" and all(s in ("ok", "unknown") for s in store_statuses):
        return "ok"
    return "degraded"


class HealthChecker:
    """Assembles production health from DB probes and event timestamps."""

    def __init__(
        self,
        session: AsyncSession | None,
        app_state: AppState,
        *,
        thresholds: HealthThresholds | None = None,
        pipeline_last_event_at: datetime | None = None,
        api_version: str = "0.1.0",
    ) -> None:
        self._session = session
        self._state = app_state
        self._thresholds = thresholds or HealthThresholds()
        self._pipeline_last = pipeline_last_event_at
        self._version = api_version

    async def check(self) -> HealthResponse:
        now = datetime.now(timezone.utc)
        checks: list[HealthCheck] = []
        degraded_features: list[str] = list(self._state.degraded_features)

        uptime = (now - self._state.started_at).total_seconds()

        db_started = time.perf_counter()
        db_ok = await check_db_connection() if self._session else False
        db_latency_ms = round((time.perf_counter() - db_started) * 1000, 2)

        if db_ok:
            self._state.mark_db_up()
        else:
            self._state.mark_db_down("postgres")
            if "postgres" not in degraded_features:
                degraded_features.append("postgres")

        db_health = DatabaseHealth(
            status="up" if db_ok else "down",
            latency_ms=db_latency_ms if db_ok else None,
            message="SELECT 1 succeeded" if db_ok else "Database unreachable",
        )
        checks.append(
            HealthCheck(
                name="database",
                status="pass" if db_ok else "fail",
                detail=db_health.message,
                observed=f"{db_latency_ms}ms" if db_ok else "timeout/error",
                threshold="connected",
            )
        )

        dependencies = [
            DependencyStatus(
                name="postgres",
                status=db_health.status,
                latency_ms=db_health.latency_ms,
                message=db_health.message,
            )
        ]

        if not db_ok or self._session is None:
            return HealthResponse(
                status="down",
                checked_at=now,
                uptime_seconds=round(uptime, 1),
                version=self._version,
                postgres="down",
                database=db_health,
                ingestion=IngestionHealth(
                    status="unknown",
                    message="Database unavailable — cannot measure ingestion lag",
                ),
                stale_feed=True,
                pipeline_last_event_at=self._pipeline_last,
                degraded_features=degraded_features,
                dependencies=dependencies,
                stores=[],
                checks=checks,
            )

        repo = HealthDataRepository(self._session)
        started = time.perf_counter()

        latest_occurred, latest_ingested = await repo.fetch_global_timestamps()
        latest_batch = await repo.fetch_latest_ingest_batch_time()
        batches_last_hour = await repo.count_ingest_batches_since(now - timedelta(hours=1))
        store_rows = await repo.fetch_per_store_timestamps()
        camera_rows = await repo.fetch_camera_last_events()

        query_ms = round((time.perf_counter() - started) * 1000, 2)
        checks.append(
            HealthCheck(
                name="health_queries",
                status="pass",
                detail="Health signal queries completed",
                observed=f"{query_ms}ms",
            )
        )

        pipeline_last = latest_occurred
        if self._pipeline_last and (pipeline_last is None or self._pipeline_last > pipeline_last):
            pipeline_last = self._pipeline_last

        latest_event_at = pipeline_last
        global_stale_min = _minutes_since(now, latest_event_at)
        _, global_stale = _check_status_from_minutes(global_stale_min, thresholds=self._thresholds)

        ingestion_lag = _seconds_since(now, latest_ingested)
        if latest_batch is not None:
            batch_lag = _seconds_since(now, latest_batch)
            if ingestion_lag is None or (batch_lag is not None and batch_lag > ingestion_lag):
                ingestion_lag = batch_lag

        ingestion_status = _ingestion_status_from_lag(ingestion_lag, thresholds=self._thresholds)
        ingestion_health = IngestionHealth(
            status=ingestion_status,
            lag_seconds=round(ingestion_lag, 1) if ingestion_lag is not None else None,
            latest_ingested_at=latest_ingested,
            latest_batch_received_at=latest_batch,
            batches_last_hour=batches_last_hour,
            message=(
                f"Ingestion lag {ingestion_lag:.0f}s"
                if ingestion_lag is not None
                else "No ingested events recorded"
            ),
        )

        checks.append(
            HealthCheck(
                name="ingestion_lag",
                status=(
                    "pass"
                    if ingestion_status == "ok"
                    else "warn" if ingestion_status == "degraded" else "fail"
                ),
                detail=ingestion_health.message,
                observed=f"{ingestion_lag:.0f}s" if ingestion_lag is not None else None,
                threshold=f"warn>{self._thresholds.ingestion_lag_warn_seconds}s",
            )
        )
        checks.append(
            HealthCheck(
                name="stale_feed_global",
                status="pass" if not global_stale else "warn",
                detail=(
                    f"Latest event {latest_event_at.isoformat()}"
                    if latest_event_at
                    else "No events in database"
                ),
                observed=f"{global_stale_min:.0f}m ago" if global_stale_min is not None else None,
                threshold=f"warn>{self._thresholds.stale_feed_minutes_warn}m",
            )
        )

        if ingestion_status != "ok":
            degraded_features.append("ingestion_lag")
        if global_stale:
            degraded_features.append("stale_feed")

        store_map: dict[str, _StoreSignals] = {}
        for store_id, occurred, ingested in store_rows:
            store_map[store_id] = _StoreSignals(
                store_id=store_id,
                latest_event_at=occurred,
                latest_ingested_at=ingested,
            )
        for store_id, camera_id, last_at in camera_rows:
            if store_id not in store_map:
                store_map[store_id] = _StoreSignals(store_id=store_id)
            store_map[store_id].cameras[camera_id] = last_at

        stores = [
            build_store_health(signals, now=now, thresholds=self._thresholds)
            for signals in store_map.values()
        ]

        overall = compute_overall_status(
            db_status=db_health.status,
            ingestion_status=ingestion_status,
            global_stale=global_stale,
            store_statuses=[s.status for s in stores],
            degraded_features=list(dict.fromkeys(degraded_features)),
        )

        logger.info(
            "health_check",
            status=overall,
            db_latency_ms=db_latency_ms,
            ingestion_lag_s=ingestion_lag,
            global_stale=global_stale,
            store_count=len(stores),
            query_ms=query_ms,
        )

        return HealthResponse(
            status=overall,
            checked_at=now,
            uptime_seconds=round(uptime, 1),
            version=self._version,
            postgres="up" if db_ok else "down",
            database=db_health,
            ingestion=ingestion_health,
            latest_event_at=latest_event_at,
            stale_feed=global_stale,
            pipeline_last_event_at=pipeline_last,
            degraded_features=list(dict.fromkeys(degraded_features)),
            dependencies=dependencies,
            stores=stores,
            checks=checks,
        )
