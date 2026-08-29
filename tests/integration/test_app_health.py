# PROMPT:
# Integration tests for FastAPI app factory — health, OpenAPI, liveness.
#
# CHANGES MADE:
# - Validates production health JSON schema without live Postgres

"""Integration tests for app.main."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

pytestmark = pytest.mark.integration


def test_health_endpoint() -> None:
    client = TestClient(create_app())
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded", "down")
    assert "postgres" in body
    assert "checked_at" in body
    assert "database" in body
    assert "ingestion" in body
    assert "checks" in body
    assert "stores" in body


def test_openapi_available() -> None:
    client = TestClient(create_app())
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Store Intelligence API"


def test_liveness() -> None:
    client = TestClient(create_app())
    assert client.get("/live").json()["status"] == "alive"
