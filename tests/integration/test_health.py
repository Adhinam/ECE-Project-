# PROMPT:
# Legacy health integration — delegates to app.main health endpoint.
#
# CHANGES MADE:
# - Smoke test via api.main re-export

"""Legacy health integration test."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.main import app

pytestmark = pytest.mark.integration


def test_health_returns_200() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "degraded", "down")
