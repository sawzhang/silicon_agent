import logging
import traceback

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:
            logger.error(
                "Unhandled exception on %s %s: %s",
                request.method,
                request.url.path,
                exc,
            )
            logger.debug(traceback.format_exc())
            from app.config import settings
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Internal server error",
                    "error": str(exc) if settings.DEBUG else "Internal server error",
                },
            )
