"""Request logging middleware with correlation ID and structured output."""
from __future__ import annotations

import logging
import time
import uuid
from contextvars import ContextVar

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger("app.access")

# ContextVar for correlation ID — accessible across async tasks in the same request
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="-")


def get_correlation_id() -> str:
    return correlation_id_var.get()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]
        correlation_id_var.set(req_id)

        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - start) * 1000

        logger.info(
            "method=%s path=%s status=%d duration_ms=%.1f req_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            req_id,
        )

        response.headers["X-Request-ID"] = req_id
        return response
