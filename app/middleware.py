"""HTTP middleware — request correlation, timing, structured access logs."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from shared.logging import get_logger

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"
TRACE_ID_HEADER = "X-Trace-ID"


def _extract_store_id(path: str) -> str | None:
    parts = path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "stores":
        return parts[1]
    return None


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Attach a request ID and emit a single access log line per request.

    Clients may pass X-Request-ID; otherwise one is generated. The ID is bound
    to structlog contextvars for the duration of the request.
    """

    async def dispatch(self, request: Request, call_next: Callable[[Request], Response]) -> Response:
        request_id = (
            request.headers.get(TRACE_ID_HEADER)
            or request.headers.get(REQUEST_ID_HEADER)
            or str(uuid.uuid4())
        )
        store_id = _extract_store_id(request.url.path)
        structlog.contextvars.clear_contextvars()
        bind: dict[str, str] = {
            "trace_id": request_id,
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "endpoint": request.url.path,
        }
        if store_id:
            bind["store_id"] = store_id
        structlog.contextvars.bind_contextvars(**bind)

        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            # Skip noise from kube probes hitting /live every few seconds at debug only
            log_fn = logger.debug if request.url.path in ("/live", "/ready") else logger.info
            log_fn(
                "http_request",
                trace_id=request_id,
                endpoint=request.url.path,
                store_id=store_id,
                status_code=status_code,
                latency_ms=duration_ms,
                duration_ms=duration_ms,
                client_host=request.client.host if request.client else None,
            )
