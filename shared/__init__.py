"""Cross-cutting utilities shared by api, pipeline, and ingest."""

from shared.logging import configure_logging, get_logger

__all__ = ["configure_logging", "get_logger"]
