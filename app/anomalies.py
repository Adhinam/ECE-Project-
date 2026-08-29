"""Anomaly detection engine — rolling baselines, explainable rules, severity scoring."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.funnel import FunnelAnalyticsEngine
from app.repositories import AnomalyDataRepository
from app.state import AppState
from schemas.api import AnomaliesResponse, AnomalyItem, AnomalySeverity
from shared.logging import get_logger

logger = get_logger(__name__)

EPSILON = 1e-6
SEVERITY_ORDER = {
    AnomalySeverity.CRITICAL: 0,
    AnomalySeverity.WARN: 1,
    AnomalySeverity.INFO: 2,
}


class AnomalyType(StrEnum):
    QUEUE_SPIKE = "queue_spike"
    CONVERSION_DROP = "conversion_drop"
    DEAD_ZONE = "dead_zone"
    STALE_FEED = "stale_feed"


class AnomalyThresholds(BaseSettings):
    """Configurable detection thresholds (env prefix ANOMALY_)."""

    model_config = SettingsConfigDict(
        env_prefix="ANOMALY_",
        env_file=".env",
        extra="ignore",
    )

    baseline_window_hours: int = Field(default=168, ge=1)
    current_window_minutes: int = Field(default=60, ge=5)
    min_baseline_samples: int = Field(default=3, ge=1)
    z_score_warn: float = Field(default=2.0, ge=0.5)
    z_score_critical: float = Field(default=3.0, ge=1.0)
    conversion_drop_warn_pct: float = Field(default=25.0, ge=0.0)
    conversion_drop_critical_pct: float = Field(default=50.0, ge=0.0)
    queue_spike_absolute_critical: int = Field(default=15, ge=1)
    dead_zone_min_baseline_visits: int = Field(default=5, ge=1)
    dead_zone_drop_critical_pct: float = Field(default=90.0, ge=0.0)
    stale_feed_minutes_warn: int = Field(default=15, ge=1)
    stale_feed_minutes_critical: int = Field(default=60, ge=1)


@dataclass(frozen=True)
class DetectedAnomaly:
    anomaly_type: AnomalyType
    metric_name: str
    severity: AnomalySeverity
    observed: float
    expected: float
    z_score: float | None
    message: str
    suggested_action: str
    details: dict = field(default_factory=dict)


def safe_mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def safe_std(values: list[float], mean: float | None = None) -> float:
    if len(values) < 2:
        return EPSILON
    m = mean if mean is not None else safe_mean(values)
    variance = sum((v - m) ** 2 for v in values) / (len(values) - 1)
    return max(variance**0.5, EPSILON)


def rolling_z_score(
    observed: float,
    history: list[float],
    *,
    min_samples: int,
) -> tuple[float, float, float]:
    """Return (z_score, baseline_mean, baseline_std). Z=0 when baseline insufficient."""
    if len(history) < min_samples:
        return 0.0, safe_mean(history), 0.0
    mean = safe_mean(history)
    std = safe_std(history, mean)
    z = (observed - mean) / std
    return z, mean, std


def severity_from_z(
    z: float,
    *,
    thresholds: AnomalyThresholds,
    relative_drop_pct: float | None = None,
) -> AnomalySeverity:
    """Map z-score (and optional relative drop) to INFO / WARN / CRITICAL."""
    abs_z = abs(z)
    if abs_z >= thresholds.z_score_critical:
        return AnomalySeverity.CRITICAL
    if relative_drop_pct is not None:
        if relative_drop_pct >= thresholds.conversion_drop_critical_pct:
            return AnomalySeverity.CRITICAL
        if relative_drop_pct >= thresholds.conversion_drop_warn_pct:
            return AnomalySeverity.WARN
    if abs_z >= thresholds.z_score_warn:
        return AnomalySeverity.WARN
    return AnomalySeverity.INFO


def detect_queue_spikes(
    *,
    current_depths: list[tuple[str, int]],
    historical_depths: list[tuple[str, int, datetime]],
    thresholds: AnomalyThresholds,
    detected_at: datetime,
) -> list[DetectedAnomaly]:
    """Flag queues whose current depth exceeds rolling baseline."""
    anomalies: list[DetectedAnomaly] = []
    history_by_queue: dict[str, list[float]] = {}
    for queue_id, depth, _at in historical_depths:
        history_by_queue.setdefault(queue_id, []).append(float(depth))

    for queue_id, observed in current_depths:
        history = history_by_queue.get(queue_id, [])
        z, baseline, std = rolling_z_score(
            float(observed),
            history,
            min_samples=thresholds.min_baseline_samples,
        )
        if len(history) < thresholds.min_baseline_samples:
            if observed >= thresholds.queue_spike_absolute_critical:
                severity = AnomalySeverity.CRITICAL
            elif observed >= thresholds.queue_spike_absolute_critical // 2:
                severity = AnomalySeverity.WARN
            else:
                continue
        else:
            severity = severity_from_z(z, thresholds=thresholds)
            if z < thresholds.z_score_warn and observed < thresholds.queue_spike_absolute_critical:
                continue

        if observed < baseline and severity == AnomalySeverity.INFO:
            continue

        anomalies.append(
            DetectedAnomaly(
                anomaly_type=AnomalyType.QUEUE_SPIKE,
                metric_name=f"queue_depth:{queue_id}",
                severity=severity,
                observed=float(observed),
                expected=baseline,
                z_score=round(z, 3) if z else None,
                message=(
                    f"Queue '{queue_id}' depth {observed} exceeds baseline "
                    f"{baseline:.1f} (σ={std:.2f}, z={z:.2f})."
                ),
                suggested_action=(
                    "Open additional checkout lane or redirect traffic away from this queue."
                    if severity == AnomalySeverity.CRITICAL
                    else "Monitor queue length and prepare staff redeployment."
                ),
                details={
                    "queue_id": queue_id,
                    "rule": "rolling_z_score",
                    "baseline_samples": len(history),
                    "baseline_mean": round(baseline, 2),
                    "baseline_std": round(std, 4),
                },
            )
        )
    return anomalies


def detect_conversion_drop(
    *,
    current_rate: float,
    baseline_rates: list[float],
    thresholds: AnomalyThresholds,
) -> DetectedAnomaly | None:
    """Alert when conversion falls vs rolling baseline (zero-safe)."""
    if not baseline_rates:
        return None

    baseline = safe_mean(baseline_rates)
    if baseline <= EPSILON:
        return None

    drop_pct = max(0.0, (baseline - current_rate) / baseline * 100.0)
    if drop_pct < thresholds.conversion_drop_warn_pct:
        return None

    z, _, _ = rolling_z_score(
        current_rate,
        baseline_rates,
        min_samples=thresholds.min_baseline_samples,
    )
    severity = severity_from_z(-abs(z), thresholds=thresholds, relative_drop_pct=drop_pct)

    return DetectedAnomaly(
        anomaly_type=AnomalyType.CONVERSION_DROP,
        metric_name="conversion_rate",
        severity=severity,
        observed=round(current_rate, 4),
        expected=round(baseline, 4),
        z_score=round(-abs(z), 3) if z else None,
        message=(
            f"Conversion {current_rate:.1%} is down {drop_pct:.1f}% vs baseline {baseline:.1%}."
        ),
        suggested_action=(
            "Review checkout staffing, queue layout, and in-store promotions near billing."
            if severity != AnomalySeverity.INFO
            else "Track conversion trend over the next hour."
        ),
        details={
            "rule": "relative_drop_vs_baseline",
            "drop_percent": round(drop_pct, 2),
            "baseline_samples": len(baseline_rates),
            "zero_purchase_safe": True,
        },
    )


def detect_dead_zones(
    *,
    current_visits: dict[str, int],
    baseline_visits: dict[str, int],
    thresholds: AnomalyThresholds,
) -> list[DetectedAnomaly]:
    """Zones active in baseline but near-zero traffic in the current window."""
    anomalies: list[DetectedAnomaly] = []
    for zone_id, baseline_count in baseline_visits.items():
        if baseline_count < thresholds.dead_zone_min_baseline_visits:
            continue
        current_count = current_visits.get(zone_id, 0)
        if current_count > 0:
            continue

        drop_pct = 100.0
        severity = (
            AnomalySeverity.CRITICAL
            if drop_pct >= thresholds.dead_zone_drop_critical_pct
            else AnomalySeverity.WARN
        )
        anomalies.append(
            DetectedAnomaly(
                anomaly_type=AnomalyType.DEAD_ZONE,
                metric_name=f"zone_visits:{zone_id}",
                severity=severity,
                observed=float(current_count),
                expected=float(baseline_count),
                z_score=None,
                message=(
                    f"Zone '{zone_id}' has no visits in the current window "
                    f"(baseline avg {baseline_count:.0f} visits/bucket)."
                ),
                suggested_action=(
                    "Check camera ROI calibration, lighting, and whether the zone was blocked "
                    "or merchandising changed."
                ),
                details={
                    "zone_id": zone_id,
                    "rule": "zero_current_with_active_baseline",
                    "baseline_visits": baseline_count,
                    "current_visits": current_count,
                },
            )
        )
    return anomalies


def detect_stale_feeds(
    *,
    camera_last_event: list[tuple[str, datetime]],
    store_last_event: datetime | None,
    now: datetime,
    thresholds: AnomalyThresholds,
) -> list[DetectedAnomaly]:
    """Cameras or store with no recent events."""
    anomalies: list[DetectedAnomaly] = []

    def _stale_minutes(last_at: datetime) -> float:
        return max(0.0, (now - last_at).total_seconds() / 60.0)

    if store_last_event is not None:
        stale_m = _stale_minutes(store_last_event)
        if stale_m >= thresholds.stale_feed_minutes_warn:
            severity = (
                AnomalySeverity.CRITICAL
                if stale_m >= thresholds.stale_feed_minutes_critical
                else AnomalySeverity.WARN
            )
            anomalies.append(
                DetectedAnomaly(
                    anomaly_type=AnomalyType.STALE_FEED,
                    metric_name="store_event_feed",
                    severity=severity,
                    observed=round(stale_m, 1),
                    expected=float(thresholds.stale_feed_minutes_warn),
                    z_score=None,
                    message=f"No store events for {stale_m:.0f} min (last at {store_last_event.isoformat()}).",
                    suggested_action=(
                        "Verify pipeline worker, camera connectivity, and ingest API health."
                    ),
                    details={
                        "rule": "store_level_silence",
                        "last_event_at": store_last_event.isoformat(),
                        "stale_minutes": round(stale_m, 1),
                    },
                )
            )
            return anomalies

    if not camera_last_event:
        anomalies.append(
            DetectedAnomaly(
                anomaly_type=AnomalyType.STALE_FEED,
                metric_name="store_event_feed",
                severity=AnomalySeverity.CRITICAL,
                observed=0.0,
                expected=0.0,
                z_score=None,
                message="No camera feeds have ever recorded events for this store.",
                suggested_action="Initialize pipeline ingestion and confirm store_id mapping.",
                details={"rule": "no_camera_history"},
            )
        )
        return anomalies

    for camera_id, last_at in camera_last_event:
        stale_m = _stale_minutes(last_at)
        if stale_m < thresholds.stale_feed_minutes_warn:
            continue
        severity = (
            AnomalySeverity.CRITICAL
            if stale_m >= thresholds.stale_feed_minutes_critical
            else AnomalySeverity.WARN
        )
        anomalies.append(
            DetectedAnomaly(
                anomaly_type=AnomalyType.STALE_FEED,
                metric_name=f"camera_feed:{camera_id}",
                severity=severity,
                observed=round(stale_m, 1),
                expected=float(thresholds.stale_feed_minutes_warn),
                z_score=None,
                message=f"Camera '{camera_id}' silent for {stale_m:.0f} min.",
                suggested_action=(
                    "Restart camera stream, check RTSP URL, and verify detector process for this feed."
                ),
                details={
                    "camera_id": camera_id,
                    "rule": "per_camera_silence",
                    "last_event_at": last_at.isoformat(),
                    "stale_minutes": round(stale_m, 1),
                },
            )
        )
    return anomalies


def to_api_items(detected: list[DetectedAnomaly], *, detected_at: datetime) -> list[AnomalyItem]:
    sorted_items = sorted(detected, key=lambda a: SEVERITY_ORDER[a.severity])
    return [
        AnomalyItem(
            id=None,
            detected_at=detected_at,
            anomaly_type=a.anomaly_type.value,
            metric_name=a.metric_name,
            severity=a.severity,
            observed_value=a.observed,
            expected_value=a.expected,
            z_score=a.z_score,
            message=a.message,
            suggested_action=a.suggested_action,
            details=a.details,
        )
        for a in sorted_items
    ]


class AnomalyDetectionEngine:
    """Live anomaly detection with rolling baselines from DB signals."""

    def __init__(
        self,
        session: AsyncSession,
        app_state: AppState,
        *,
        thresholds: AnomalyThresholds | None = None,
    ) -> None:
        self._session = session
        self._state = app_state
        self._data = AnomalyDataRepository(session)
        self._funnel = FunnelAnalyticsEngine(session, app_state)
        self._thresholds = thresholds or AnomalyThresholds()

    async def detect(
        self,
        store_id: str,
        *,
        limit: int = 20,
        checkout_zone_id: str = "checkout",
        billing_queue_id: str = "checkout-1",
    ) -> AnomaliesResponse:
        now = datetime.now(timezone.utc)
        thresholds = self._thresholds
        current_from = now - timedelta(minutes=thresholds.current_window_minutes)
        baseline_from = now - timedelta(hours=thresholds.baseline_window_hours)

        if not self._state.db_available:
            logger.warning("anomaly_detection_degraded", store_id=store_id)
            return AnomaliesResponse(
                store_id=store_id,
                as_of=now,
                baseline_window_hours=thresholds.baseline_window_hours,
                current_window_minutes=thresholds.current_window_minutes,
                items=[],
            )

        started = time.perf_counter()
        detected: list[DetectedAnomaly] = []

        baseline_buckets = await self._data.fetch_metric_buckets(store_id, baseline_from, now)
        baseline_conversion = [
            float(b.conversion_rate)
            for b in baseline_buckets
            if b.conversion_rate is not None
        ]
        baseline_queue_depths = [
            float(b.avg_queue_depth)
            for b in baseline_buckets
            if b.avg_queue_depth is not None
        ]

        funnel_current = await self._funnel.get_funnel(
            store_id,
            from_time=current_from,
            to_time=now,
            checkout_zone_id=checkout_zone_id,
            billing_queue_id=billing_queue_id,
        )
        current_conversion = funnel_current.conversion_rate

        conv_anomaly = detect_conversion_drop(
            current_rate=current_conversion,
            baseline_rates=baseline_conversion,
            thresholds=thresholds,
        )
        if conv_anomaly is not None and not funnel_current.is_empty:
            detected.append(conv_anomaly)

        queue_samples = await self._data.fetch_queue_depth_samples(store_id, baseline_from, now)
        latest_by_queue: dict[str, int] = {}
        for qid, depth, at in queue_samples:
            if at >= current_from:
                latest_by_queue[qid] = depth
        current_depths = list(latest_by_queue.items())

        detected.extend(
            detect_queue_spikes(
                current_depths=current_depths,
                historical_depths=queue_samples,
                thresholds=thresholds,
                detected_at=now,
            )
        )

        if not queue_samples and baseline_queue_depths:
            observed = safe_mean(baseline_queue_depths[-3:]) if baseline_queue_depths else 0.0
            z, baseline, std = rolling_z_score(
                observed,
                baseline_queue_depths[:-1] or baseline_queue_depths,
                min_samples=thresholds.min_baseline_samples,
            )
            if len(baseline_queue_depths) >= thresholds.min_baseline_samples and z >= thresholds.z_score_warn:
                detected.append(
                    DetectedAnomaly(
                        anomaly_type=AnomalyType.QUEUE_SPIKE,
                        metric_name="queue_depth:aggregate",
                        severity=severity_from_z(z, thresholds=thresholds),
                        observed=observed,
                        expected=baseline,
                        z_score=round(z, 3),
                        message=f"Aggregate queue depth {observed:.1f} above baseline {baseline:.1f}.",
                        suggested_action="Review store-wide queue staffing.",
                        details={"rule": "metric_bucket_baseline", "baseline_std": round(std, 4)},
                    )
                )

        current_zones = await self._data.fetch_zone_visit_counts(store_id, current_from, now)
        baseline_zones = await self._data.fetch_zone_visit_counts(store_id, baseline_from, current_from)
        detected.extend(
            detect_dead_zones(
                current_visits=current_zones,
                baseline_visits=baseline_zones,
                thresholds=thresholds,
            )
        )

        camera_feeds = await self._data.fetch_latest_event_per_camera(store_id)
        store_last = await self._data.fetch_latest_store_event(store_id)
        detected.extend(
            detect_stale_feeds(
                camera_last_event=camera_feeds,
                store_last_event=store_last,
                now=now,
                thresholds=thresholds,
            )
        )

        items = to_api_items(detected, detected_at=now)[:limit]

        latency_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "anomalies_detected",
            store_id=store_id,
            count=len(items),
            critical=sum(1 for i in items if i.severity == AnomalySeverity.CRITICAL),
            latency_ms=round(latency_ms, 2),
        )

        return AnomaliesResponse(
            store_id=store_id,
            as_of=now,
            baseline_window_hours=thresholds.baseline_window_hours,
            current_window_minutes=thresholds.current_window_minutes,
            items=items,
        )
