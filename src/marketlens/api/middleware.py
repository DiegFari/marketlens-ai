"""Per-request context: correlation id, access logging, last-resort error handling."""

from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from marketlens.api.errors import error_response
from marketlens.core.logging import request_id_ctx

logger = logging.getLogger("marketlens.access")

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller-supplied id only if it is safe to echo into logs and headers.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_QUIET_PATH_PREFIX = "/health"


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception:
            # Handled here rather than in an exception handler so the log line and the
            # response still carry the request id.
            logger.exception("unhandled error")
            response = error_response(500, "internal_error", "An unexpected error occurred.")

        response.headers[REQUEST_ID_HEADER] = request_id
        # Health probes hit every few seconds; keep them out of INFO logs.
        level = logging.DEBUG if request.url.path.startswith(_QUIET_PATH_PREFIX) else logging.INFO
        logger.log(
            level,
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            },
        )
        request_id_ctx.reset(token)
        return response
