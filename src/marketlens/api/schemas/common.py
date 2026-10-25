from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    environment: str
    llm_provider: str
    # Which integrations have credentials. Never includes the values themselves.
    integrations: dict[str, bool]
