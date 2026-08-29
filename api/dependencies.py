"""Backward-compatible DI — delegates to app.dependencies."""

from app.dependencies import (
    get_analytics_service,
    get_app_state,
    get_db,
    get_ingestion_service,
    get_metrics_service,
    get_settings,
    verify_api_key,
)
from app.settings import ApiSettings, Settings

__all__ = [
    "ApiSettings",
    "Settings",
    "get_analytics_service",
    "get_app_state",
    "get_db",
    "get_ingestion_service",
    "get_metrics_service",
    "get_settings",
    "verify_api_key",
]
