"""HTTP ingest client — batch POST with retries."""

from __future__ import annotations

from uuid import uuid4

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from schemas.api import EventIngestRequest, EventIngestResponse
from schemas.events import EventEnvelope
from shared.logging import get_logger

logger = get_logger(__name__)


class IngestClient:
    def __init__(self, base_url: str, *, api_key: str | None = None, max_retries: int = 3) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._max_retries = max_retries

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=8))
    async def post_batch(self, events: list[EventEnvelope]) -> EventIngestResponse:
        headers = {}
        if self._api_key:
            headers["X-API-Key"] = self._api_key
        body = EventIngestRequest(
            batch_id=uuid4(),
            events=[e.model_dump(mode="json") for e in events],
        )
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f"{self._base_url}/events/ingest",
                json=body.model_dump(mode="json"),
                headers=headers,
            )
            response.raise_for_status()
            return EventIngestResponse.model_validate(response.json())
