"""REST API request/response DTOs — mirrors OpenAPI served by FastAPI."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from enum import StrEnum

from pydantic import BaseModel, Field

from schemas.events import EventEnvelope


class MetricConfidence(StrEnum):
    """Data-quality indicator for a computed metric."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNAVAILABLE = "unavailable"


MAX_INGEST_BATCH_SIZE = 500


class EventBatchRequest(BaseModel):
    batch_id: UUID | None = None
    events: list[EventEnvelope] = Field(min_length=1, max_length=MAX_INGEST_BATCH_SIZE)


class EventIngestRequest(BaseModel):
    """Raw batch ingest — each event validated independently for partial success."""

    batch_id: UUID | None = None
    events: list[dict[str, Any]] = Field(
        min_length=1,
        max_length=MAX_INGEST_BATCH_SIZE,
        description="Up to 500 event objects; malformed items are rejected without failing the batch.",
    )


class IngestError(BaseModel):
    event_id: UUID | None = None
    index: int | None = Field(default=None, description="Zero-based index in the request batch")
    message: str
    code: str = "validation_error"


class EventIngestResponse(BaseModel):
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    validation_errors: list[IngestError] = Field(default_factory=list)
    batch_id: UUID | None = None


class EventBatchResponse(BaseModel):
    accepted: int = 0
    duplicates: int = 0
    rejected: int = 0
    errors: list[IngestError] = Field(default_factory=list)

    @classmethod
    def from_ingest(cls, response: EventIngestResponse) -> EventBatchResponse:
        return cls(
            accepted=response.accepted,
            duplicates=response.duplicates,
            rejected=response.rejected,
            errors=response.validation_errors,
        )


class RealtimeMetricsResponse(BaseModel):
    store_id: str
    as_of: datetime
    visitors_inside: int = 0
    entries: int = 0
    exits: int = 0
    conversion_rate: float = 0.0
    avg_queue_depth: float | None = None
    max_queue_depth: float | None = None
    staff_excluded_count: int = 0


class ZoneDwellMetric(BaseModel):
    zone_id: str
    avg_dwell_seconds: float = 0.0
    sample_count: int = 0
    confidence: MetricConfidence = MetricConfidence.UNAVAILABLE


class QueueDepthMetric(BaseModel):
    queue_id: str
    depth: int = 0
    as_of: datetime | None = None
    confidence: MetricConfidence = MetricConfidence.UNAVAILABLE


class StoreMetricsConfidence(BaseModel):
    """Per-metric confidence flags for the store metrics payload."""

    unique_visitors: MetricConfidence = MetricConfidence.UNAVAILABLE
    conversion_rate: MetricConfidence = MetricConfidence.UNAVAILABLE
    abandonment_rate: MetricConfidence = MetricConfidence.UNAVAILABLE
    zone_dwell: MetricConfidence = MetricConfidence.UNAVAILABLE
    queue_depth: MetricConfidence = MetricConfidence.UNAVAILABLE


class StoreMetricsResponse(BaseModel):
    store_id: str
    as_of: datetime
    window_minutes: int = 15
    unique_visitors: int = 0
    conversion_rate: float = Field(
        0.0,
        description="Share of visitor sessions reaching checkout; 0.0 when no visitors or no purchases.",
    )
    abandonment_rate: float = Field(
        0.0,
        description="Share of visitors who entered but left without checkout (session-aware).",
    )
    zones: list[ZoneDwellMetric] = Field(default_factory=list)
    queues: list[QueueDepthMetric] = Field(default_factory=list)
    visitors_inside: int = 0
    staff_excluded_count: int = 0
    is_empty: bool = Field(
        False,
        description="True when no visitor activity in the window (safe defaults applied).",
    )
    has_purchases: bool = Field(
        False,
        description="True when at least one visitor session reached the checkout zone.",
    )
    confidence: StoreMetricsConfidence = Field(default_factory=StoreMetricsConfidence)
    pos_conversion_rate: float | None = Field(
        default=None,
        description="Conversion from POS transactions when data/pos_transactions.csv is present.",
    )
    pos_transaction_count: int = Field(
        default=0,
        description="POS rows in the metrics window used for correlation.",
    )


class FunnelStage(BaseModel):
    stage: str
    count: int = 0
    dropoff_percent: float = Field(
        0.0,
        description="Percent lost to the next stage: (count - next_count) / count × 100.",
    )
    conversion_percent: float = Field(
        0.0,
        description="Percent of ENTRY sessions reaching this stage: count / entry × 100.",
    )


class FunnelResponse(BaseModel):
    store_id: str
    from_time: datetime
    to_time: datetime
    stages: list[FunnelStage]
    conversion_rate: float = Field(
        0.0,
        description="ENTRY → PURCHASE conversion (same as final stage conversion_percent).",
    )
    is_empty: bool = Field(
        False,
        description="True when no visitor sessions in the window.",
    )
    staff_excluded_count: int = 0
    reentry_sessions_merged: int = Field(
        0,
        description="Sessions resumed via re-entry deduplication (not double-counted at ENTRY).",
    )


class HeatmapCell(BaseModel):
    x: int
    y: int
    visits: int
    visits_normalized: int = Field(
        0,
        ge=0,
        le=100,
        description="Visit intensity scaled 0–100 vs peak cell in this response.",
    )
    dwell_seconds: float = 0.0


class HeatmapResponse(BaseModel):
    store_id: str
    resolution: int
    from_time: datetime
    to_time: datetime
    cells: list[HeatmapCell]
    data_confidence: MetricConfidence = Field(
        default=MetricConfidence.UNAVAILABLE,
        description="Quality of heatmap based on spatial sample count.",
    )


class QueueTimeseriesPoint(BaseModel):
    timestamp: datetime
    depth: int


class QueueAnalyticsResponse(BaseModel):
    store_id: str
    queue_id: str
    current_depth: int = 0
    avg_wait_seconds: float | None = None
    timeseries: list[QueueTimeseriesPoint] = Field(default_factory=list)


class AnomalySeverity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class AnomalyItem(BaseModel):
    id: int | None = Field(default=None, description="DB id when persisted; null for live detection")
    detected_at: datetime
    anomaly_type: str = Field(description="queue_spike | conversion_drop | dead_zone | stale_feed")
    metric_name: str
    severity: AnomalySeverity
    observed_value: float
    expected_value: float | None = None
    z_score: float | None = None
    message: str = Field(description="Human-readable explanation")
    suggested_action: str = Field(default="", description="Recommended operator action")
    details: dict[str, Any] | None = None


class AnomaliesResponse(BaseModel):
    store_id: str
    as_of: datetime | None = None
    baseline_window_hours: int = 168
    current_window_minutes: int = 60
    items: list[AnomalyItem] = Field(default_factory=list)


class DependencyStatus(BaseModel):
    name: str
    status: str  # up | down | degraded
    latency_ms: float | None = None
    message: str = ""


class DatabaseHealth(BaseModel):
    status: str  # up | down
    latency_ms: float | None = None
    message: str = ""


class IngestionHealth(BaseModel):
    status: str  # ok | degraded | down | unknown
    lag_seconds: float | None = None
    latest_ingested_at: datetime | None = None
    latest_batch_received_at: datetime | None = None
    batches_last_hour: int = 0
    message: str = ""


class StoreHealth(BaseModel):
    store_id: str
    status: str  # ok | degraded | down | unknown
    latest_event_at: datetime | None = None
    latest_ingested_at: datetime | None = None
    stale_feed: bool = False
    stale_minutes: float | None = None
    ingestion_lag_seconds: float | None = None
    camera_count: int = 0
    stale_cameras: list[str] = Field(default_factory=list)
    message: str = ""


class HealthCheck(BaseModel):
    """Structured check for operational debugging."""

    name: str
    status: str  # pass | warn | fail
    detail: str = ""
    observed: str | None = None
    threshold: str | None = None


class HealthResponse(BaseModel):
    """Production health payload — overall status plus per-component detail."""

    status: str  # ok | degraded | down
    checked_at: datetime
    uptime_seconds: float = 0.0
    version: str = "0.1.0"
    postgres: str = "unknown"
    database: DatabaseHealth = Field(default_factory=lambda: DatabaseHealth(status="unknown"))
    ingestion: IngestionHealth = Field(default_factory=lambda: IngestionHealth(status="unknown"))
    latest_event_at: datetime | None = None
    stale_feed: bool = False
    pipeline_last_event_at: datetime | None = None
    degraded_features: list[str] = Field(default_factory=list)
    dependencies: list[DependencyStatus] = Field(default_factory=list)
    stores: list[StoreHealth] = Field(default_factory=list)
    checks: list[HealthCheck] = Field(default_factory=list)
