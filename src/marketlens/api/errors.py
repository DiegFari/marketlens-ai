"""Domain exceptions and the uniform JSON error envelope.

Every error response has the shape::

    {"error": {"code": "...", "message": "...", "request_id": "...", "details": ...}}

so clients (the Streamlit UI, or anything else) handle failures one way.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from marketlens.api.schemas.common import ErrorBody, ErrorResponse
from marketlens.core.logging import request_id_ctx
from marketlens.services.errors import (
    DataProviderError,
    ProviderNotConfiguredError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    TickerNotFoundError,
)

logger = logging.getLogger(__name__)


class MarketLensError(Exception):
    """Base class for errors that map to a specific HTTP response."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, *, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class LLMNotConfiguredApiError(MarketLensError):
    status_code = 503
    code = "llm_not_configured"


class AnalysisTimeoutError(MarketLensError):
    status_code = 504
    code = "analysis_timeout"


class InsufficientDataError(MarketLensError):
    status_code = 422
    code = "insufficient_data"


# Provider error -> (HTTP status, code, client-facing message). Provider wording is
# logged, not returned: it can mention plans, URLs or internal details.
_PROVIDER_ERRORS: dict[type[DataProviderError], tuple[int, str, str]] = {
    TickerNotFoundError: (404, "ticker_not_found", "No market data was found for this ticker."),
    ProviderNotConfiguredError: (
        503,
        "provider_not_configured",
        "A data provider required for this request is not configured.",
    ),
    ProviderRateLimitError: (
        503,
        "upstream_rate_limited",
        "A data provider's rate limit or quota was reached. Try again later.",
    ),
    ProviderUnavailableError: (
        503,
        "upstream_unavailable",
        "A data provider is temporarily unavailable. Try again shortly.",
    ),
    ProviderPermissionError: (
        502,
        "upstream_plan_restricted",
        "This data is not included in the configured data provider plan.",
    ),
}
_DEFAULT_PROVIDER_ERROR = (502, "upstream_error", "A data provider returned an unexpected error.")


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Any = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code, message=message, request_id=request_id_ctx.get(), details=details
        )
    )
    return JSONResponse(status_code=status_code, content=jsonable_encoder(body), headers=headers)


async def _handle_marketlens_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, MarketLensError)  # noqa: S101 (narrowing for mypy)
    return error_response(exc.status_code, exc.code, exc.message, exc.details)


async def _handle_validation_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    # Raw pydantic errors carry exception objects in ``ctx``; expose only the stable fields.
    details = [
        {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in exc.errors()
    ]
    return error_response(422, "validation_error", "Request validation failed.", details)


async def _handle_provider_error(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DataProviderError)  # noqa: S101
    status, code, message = next(
        (_PROVIDER_ERRORS[cls] for cls in type(exc).__mro__ if cls in _PROVIDER_ERRORS),
        _DEFAULT_PROVIDER_ERROR,
    )
    logger.warning(
        "data provider error",
        extra={
            "provider": exc.provider,
            "error": type(exc).__name__,
            "provider_message": exc.message,
            "upstream_status": exc.status_code,
        },
    )
    headers = None
    if isinstance(exc, ProviderRateLimitError) and exc.retry_after is not None:
        headers = {"Retry-After": str(math.ceil(exc.retry_after))}
    return error_response(status, code, message, {"provider": exc.provider}, headers)


_HTTP_ERROR_CODES = {404: "not_found", 405: "method_not_allowed"}


async def _handle_http_exception(_: Request, exc: Exception) -> JSONResponse:
    # Routing errors (unknown path, wrong method) come from Starlette; without this they
    # bypass the envelope and return {"detail": ...}.
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    code = _HTTP_ERROR_CODES.get(exc.status_code, "http_error")
    # exc.headers carries e.g. the Allow header required on 405 responses.
    return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)
    app.add_exception_handler(MarketLensError, _handle_marketlens_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(DataProviderError, _handle_provider_error)
