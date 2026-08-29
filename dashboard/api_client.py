"""Typed HTTP client for dashboard → API (connection pooling, low latency)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from schemas.api import (
    AnomaliesResponse,
    FunnelResponse,
    HeatmapResponse,
    HealthResponse,
    QueueAnalyticsResponse,
    RealtimeMetricsResponse,
    StoreMetricsResponse,
)


class DashboardApiClient:
    def __init__(self, base_url: str, *, timeout: float = 4.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.Client(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout, connect=2.0),
            limits=httpx.Limits(max_keepalive_connections=8, max_connections=16),
        )

    def close(self) -> None:
        self._client.close()

    def get_health(self) -> HealthResponse:
        r = self._client.get("/health")
        r.raise_for_status()
        return HealthResponse.model_validate(r.json())

    def get_store_metrics(
        self,
        store_id: str,
        *,
        window_minutes: int = 15,
    ) -> StoreMetricsResponse:
        r = self._client.get(
            f"/stores/{store_id}/metrics",
            params={"window_minutes": window_minutes},
        )
        r.raise_for_status()
        return StoreMetricsResponse.model_validate(r.json())

    def get_realtime_metrics(self, store_id: str, window_minutes: int = 15) -> RealtimeMetricsResponse:
        r = self._client.get(
            "/v1/metrics/realtime",
            params={"store_id": store_id, "window_minutes": window_minutes},
        )
        r.raise_for_status()
        return RealtimeMetricsResponse.model_validate(r.json())

    def get_anomalies(self, store_id: str, limit: int = 20) -> AnomaliesResponse:
        r = self._client.get(
            f"/stores/{store_id}/anomalies",
            params={"limit": limit},
        )
        r.raise_for_status()
        return AnomaliesResponse.model_validate(r.json())

    def get_funnel(self, store_id: str, hours: int = 24) -> FunnelResponse:
        now = datetime.now(timezone.utc)
        from_time = now - timedelta(hours=hours)
        r = self._client.get(
            f"/stores/{store_id}/funnel",
            params={"from": from_time.isoformat(), "to": now.isoformat()},
        )
        r.raise_for_status()
        return FunnelResponse.model_validate(r.json())

    def get_queue(self, store_id: str, queue_id: str) -> QueueAnalyticsResponse:
        r = self._client.get(
            "/v1/analytics/queue",
            params={"store_id": store_id, "queue_id": queue_id, "window_minutes": 60},
        )
        r.raise_for_status()
        return QueueAnalyticsResponse.model_validate(r.json())

    def get_heatmap(self, store_id: str, *, window_minutes: int = 60, resolution: int = 32) -> HeatmapResponse:
        r = self._client.get(
            f"/stores/{store_id}/heatmap",
            params={"window_minutes": window_minutes, "resolution": resolution},
        )
        r.raise_for_status()
        return HeatmapResponse.model_validate(r.json())

    def fetch_live_snapshot(
        self,
        store_id: str,
        *,
        window_minutes: int = 15,
        anomaly_limit: int = 15,
    ) -> dict[str, Any]:
        """Parallel fetch for dashboard refresh."""
        results: dict[str, Any] = {
            "metrics": None,
            "anomalies": None,
            "health": None,
            "funnel": None,
            "heatmap": None,
            "errors": {},
        }

        def _metrics() -> StoreMetricsResponse:
            return self.get_store_metrics(store_id, window_minutes=window_minutes)

        def _anomalies() -> AnomaliesResponse:
            return self.get_anomalies(store_id, limit=anomaly_limit)

        def _health() -> HealthResponse:
            return self.get_health()

        def _funnel() -> FunnelResponse:
            return self.get_funnel(store_id, hours=max(1, window_minutes // 60))

        def _heatmap() -> HeatmapResponse:
            return self.get_heatmap(store_id, window_minutes=max(15, window_minutes), resolution=32)

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(_metrics): "metrics",
                pool.submit(_anomalies): "anomalies",
                pool.submit(_health): "health",
                pool.submit(_funnel): "funnel",
                pool.submit(_heatmap): "heatmap",
            }
            for future in as_completed(futures):
                key = futures[future]
                try:
                    results[key] = future.result()
                except Exception as exc:
                    results["errors"][key] = str(exc)

        return results
