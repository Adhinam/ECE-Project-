# PROMPT:
# Ingest HTTP client tests with httpx AsyncClient mock.
#
# CHANGES MADE:
# - IngestClient.post_batch async test with mocked response

"""Ingest client tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ingest.client import IngestClient
from schemas.api import EventIngestResponse
from tests.factories import EventFactory


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ingest_client_post_batch(event_factory: EventFactory) -> None:
    client = IngestClient("http://test")
    mock_response = MagicMock()
    mock_response.json.return_value = EventIngestResponse(
        accepted=1, rejected=0, duplicates=0
    ).model_dump()
    mock_response.raise_for_status = MagicMock()

    mock_http = AsyncMock()
    mock_http.post = AsyncMock(return_value=mock_response)
    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_http)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with patch("ingest.client.httpx.AsyncClient", return_value=mock_cm):
        result = await client.post_batch([event_factory.entry()])
    assert result.accepted == 1
