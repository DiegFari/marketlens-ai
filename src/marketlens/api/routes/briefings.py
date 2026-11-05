"""Public endpoints used by the web frontend.

* ``POST /briefings/stream``: free-text request in, Server-Sent Events out, so the page
  can show each agent working before the briefing arrives.
* ``GET /briefings/quota``: how many live demo briefings are left today.
* ``GET /examples`` and ``GET /examples/{ticker}``: saved briefings, always available.

Event stream (each ``data:`` is JSON)::

    event: resolved      {"ticker": "AMZN", "company_name": "Amazon", "cached": false}
    event: queued        {"message": "..."}   (another briefing is running; waiting)
    event: step          {"step": "news_analyst", "status": "running", ...}
    event: briefing      <BriefingResponse>
    event: clarification {"message": "Which Mercury do you mean?"}
    event: error         {"code": "ticker_not_found", "message": "..."}
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from functools import lru_cache
from importlib import resources
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from marketlens.agents.resolver import match_ticker, resolve_request
from marketlens.agents.state import ProgressEvent
from marketlens.api.briefings_service import generate_briefing
from marketlens.api.demo import DemoLimitError
from marketlens.api.dependencies import DemoGuardDep, LLMDep, SettingsDep, WorkflowDep
from marketlens.api.errors import MarketLensError, describe_provider_error
from marketlens.api.routes.analysis import llm_not_configured
from marketlens.api.schemas.analysis import BriefingResponse, ResolveRequest
from marketlens.api.schemas.common import ErrorResponse
from marketlens.services.errors import DataProviderError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["briefings"])

HEARTBEAT_SECONDS = 15.0
_DONE = object()


class QuotaResponse(BaseModel):
    limits_enabled: bool
    remaining_today: int | None


class ExampleSummary(BaseModel):
    ticker: str
    company_name: str | None
    generated_at: str


# ---------------------------------------------------------------------- stream


@router.post("/briefings/stream", response_class=StreamingResponse)
async def stream_briefing(
    body: ResolveRequest,
    request: Request,
    workflow: WorkflowDep,
    llm: LLMDep,
    guard: DemoGuardDep,
    settings: SettingsDep,
) -> StreamingResponse:
    """Resolve a free-text request and stream the agents' progress, then the briefing."""
    queue: asyncio.Queue[Any] = asyncio.Queue()
    client = request.client.host if request.client else "unknown"

    async def emit(event: str, data: Any) -> None:
        await queue.put((event, data))

    async def on_progress(event: ProgressEvent) -> None:
        await emit("step", _public_step(event.model_dump(mode="json")))

    async def on_queued() -> None:
        await emit("queued", {"message": "Another briefing is running; you're next in line."})

    async def produce() -> None:
        try:
            if workflow is None:
                raise llm_not_configured(settings.llm.provider)
            ticker = match_ticker(body.message)
            # Repeats of a recent briefing are free: no limits, no model calls.
            if ticker is None or guard.cached(ticker) is None:
                guard.charge_client(client)
            company_name: str | None = None
            if ticker is None:
                resolved = await resolve_request(body.message, llm)
                if resolved.ticker is None:
                    await emit("clarification", {"message": resolved.clarification})
                    return
                ticker, company_name = resolved.ticker, resolved.company_name
            await emit(
                "resolved",
                {
                    "ticker": ticker,
                    "company_name": company_name,
                    "cached": guard.cached(ticker) is not None,
                },
            )
            briefing = await generate_briefing(
                ticker,
                workflow=workflow,
                guard=guard,
                timeout_seconds=settings.agents.workflow_timeout_seconds,
                on_progress=on_progress,
                on_queued=on_queued,
            )
            public = briefing.model_dump(mode="json")
            public["trace"] = [_public_step(step) for step in public["trace"]]
            await emit("briefing", public)
        except Exception as exc:  # every failure becomes an error event
            await emit("error", _describe(exc))
        finally:
            await queue.put(_DONE)

    # The run continues if the visitor disconnects, so its result still lands in cache.
    task = asyncio.create_task(produce())
    background: set[asyncio.Task[None]] = request.app.state.background
    background.add(task)
    task.add_done_callback(background.discard)

    return StreamingResponse(
        _sse(queue),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _sse(queue: asyncio.Queue[Any]) -> AsyncIterator[str]:
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), HEARTBEAT_SECONDS)
        except TimeoutError:
            yield ": keep-alive\n\n"  # comment line; stops proxies closing an idle stream
            continue
        if item is _DONE:
            return
        event, data = item
        yield f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _public_step(step: dict[str, Any]) -> dict[str, Any]:
    """Failure details carry raw provider errors (org ids, limits): keep them in logs only."""
    if step.get("status") == "failed":
        return {**step, "detail": "This step failed; the briefing continues without it."}
    return step


def _describe(exc: Exception) -> dict[str, str]:
    if isinstance(exc, DemoLimitError | MarketLensError):
        return {"code": exc.code, "message": exc.message}
    if isinstance(exc, DataProviderError):
        _, code, message = describe_provider_error(exc)
        return {"code": code, "message": message}
    logger.exception("briefing stream failed")
    return {"code": "internal_error", "message": "Something went wrong. Please try again."}


# ---------------------------------------------------------------------- quota


@router.get("/briefings/quota", response_model=QuotaResponse)
async def quota(guard: DemoGuardDep, settings: SettingsDep) -> QuotaResponse:
    return QuotaResponse(
        limits_enabled=settings.demo.limits_enabled, remaining_today=guard.remaining_today
    )


# ---------------------------------------------------------------------- examples


@lru_cache
def load_examples() -> dict[str, BriefingResponse]:
    """Saved briefings bundled with the package (``marketlens/examples/*.json``)."""
    examples: dict[str, BriefingResponse] = {}
    for entry in sorted(resources.files("marketlens.examples").iterdir(), key=lambda p: p.name):
        if entry.name.endswith(".json"):
            briefing = BriefingResponse.model_validate_json(entry.read_text(encoding="utf-8"))
            examples[briefing.ticker] = briefing
    return examples


@router.get("/examples", response_model=list[ExampleSummary])
async def list_examples() -> list[ExampleSummary]:
    return [
        ExampleSummary(
            ticker=b.ticker, company_name=b.company_name, generated_at=b.generated_at.isoformat()
        )
        for b in load_examples().values()
    ]


@router.get(
    "/examples/{ticker}",
    response_model=BriefingResponse,
    responses={404: {"model": ErrorResponse, "description": "No saved example"}},
)
async def get_example(ticker: str) -> BriefingResponse:
    example = load_examples().get(ticker.upper())
    if example is None:
        raise ExampleNotFoundError(f"No saved example briefing for {ticker.upper()}.")
    return example


class ExampleNotFoundError(MarketLensError):
    status_code = 404
    code = "example_not_found"
