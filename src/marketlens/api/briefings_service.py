"""Produce a briefing for a ticker: cache first, then a guarded workflow run."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from marketlens.agents import PROMPT_VERSION, BriefingStatus, BriefingWorkflow, WorkflowTimeoutError
from marketlens.agents.graph import ProgressCallback
from marketlens.api.demo import DemoGuard
from marketlens.api.errors import (
    AnalysisTimeoutError,
    InsufficientDataError,
    ModelUnavailableError,
)
from marketlens.api.schemas.analysis import BriefingResponse
from marketlens.services.errors import TickerNotFoundError

logger = logging.getLogger(__name__)


async def generate_briefing(
    ticker: str,
    *,
    workflow: BriefingWorkflow,
    guard: DemoGuard,
    timeout_seconds: float,
    on_progress: ProgressCallback | None = None,
    on_queued: Callable[[], Awaitable[None]] | None = None,
) -> BriefingResponse:
    """Return a fresh or recently cached briefing.

    Raises ``DemoLimitError`` when a limit is reached, ``TickerNotFoundError`` (404),
    ``InsufficientDataError`` (422) or ``AnalysisTimeoutError`` (504).
    """
    if (hit := guard.cached(ticker)) is not None:
        logger.info("briefing served from cache", extra={"ticker": ticker})
        cached: BriefingResponse = hit.model_copy(update={"cached": True})
        return cached

    if guard.is_busy and on_queued is not None:
        await on_queued()
    await guard.acquire_briefing()
    try:
        # Another request may have produced this briefing while we waited in the queue.
        if (hit := guard.cached(ticker)) is not None:
            fresh: BriefingResponse = hit.model_copy(update={"cached": True})
            return fresh
        logger.info("briefing requested", extra={"ticker": ticker})
        try:
            state = await workflow.run(ticker, on_progress=on_progress)
        except WorkflowTimeoutError as exc:
            raise AnalysisTimeoutError(
                f"The briefing did not complete within {timeout_seconds:g} seconds."
            ) from exc
    finally:
        await guard.release_briefing()

    if state.status == BriefingStatus.INSUFFICIENT_DATA:
        if state.ticker_not_found:
            raise TickerNotFoundError("financial_modeling_prep", ticker)
        raise InsufficientDataError(
            f"No market data, company profile or news could be retrieved for {ticker}.",
            details=[w.model_dump() for w in state.warnings],
        )

    if state.market_analysis is None and state.news_analysis is None and state.briefing is None:
        # Every agent failed (typically the model's quota or an outage): a briefing with
        # no analysis at all is not worth showing, and must not be cached.
        logger.warning("no agent produced output", extra={"ticker": ticker})
        raise ModelUnavailableError(
            "The AI model is unavailable right now (its free usage quota may be used up "
            "for today). The saved example briefings are still available."
        )

    logger.info(
        "briefing completed",
        extra={"ticker": ticker, "status": state.status, "warnings": len(state.warnings)},
    )
    response = BriefingResponse.from_state(
        state, model=workflow.model_name, prompt_version=PROMPT_VERSION
    )
    # Only complete briefings are reused; a partial one may reflect a passing outage.
    if response.status == BriefingStatus.COMPLETE:
        guard.store(ticker, response)
    return response
