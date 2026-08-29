"""Backward-compatible entry — use `app.main` as the canonical application."""

from app.main import app, create_app

__all__ = ["app", "create_app"]
