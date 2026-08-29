"""Contract-first schemas — single source of truth for events and API DTOs."""

from schemas.api import (
    AnomaliesResponse,
    EventBatchRequest,
    EventBatchResponse,
    FunnelResponse,
    HealthResponse,
    HeatmapResponse,
    IngestError,
    QueueAnalyticsResponse,
    RealtimeMetricsResponse,
)
from schemas.config import StoreLayoutConfig
from schemas.events import (
    EventEnvelope,
    EventType,
    generate_event_id,
)

__all__ = [
    "AnomaliesResponse",
    "EventBatchRequest",
    "EventBatchResponse",
    "EventEnvelope",
    "EventType",
    "FunnelResponse",
    "HealthResponse",
    "HeatmapResponse",
    "IngestError",
    "QueueAnalyticsResponse",
    "RealtimeMetricsResponse",
    "StoreLayoutConfig",
    "generate_event_id",
]
