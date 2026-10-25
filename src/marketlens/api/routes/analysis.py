"""Executive briefing endpoint: runs the LangGraph multi-agent workflow for a ticker."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from marketlens.agents import PROMPT_VERSION, BriefingStatus, WorkflowTimeoutError
from marketlens.api.dependencies import SettingsDep, WorkflowDep
from marketlens.api.errors import (
    AnalysisTimeoutError,
    InsufficientDataError,
    LLMNotConfiguredApiError,
)
from marketlens.api.schemas.analysis import AnalysisRequest, BriefingResponse
from marketlens.api.schemas.common import ErrorResponse
from marketlens.services.errors import TickerNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post(
    "",
    response_model=BriefingResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Ticker unknown to the data providers"},
        422: {"model": ErrorResponse, "description": "Invalid ticker, or no usable data"},
        503: {"model": ErrorResponse, "description": "Azure OpenAI is not configured"},
        504: {"model": ErrorResponse, "description": "Briefing took longer than the time limit"},
    },
)
async def create_analysis(
    request: AnalysisRequest, workflow: WorkflowDep, settings: SettingsDep
) -> BriefingResponse:
    """Generate an executive market briefing.

    Partial failures (a provider or an agent failing) still return 200 with
    ``status="partial"`` and structured ``warnings``; see the workflow docs.
    """
    # Checked here rather than in the dependency, so invalid input still gets a 422.
    if workflow is None:
        raise LLMNotConfiguredApiError(
            f"Briefings need a language model, but LLM_PROVIDER={settings.llm.provider} is "
            "not configured (e.g. set GROQ_API_KEY; see README)."
        )

    logger.info("briefing requested", extra={"ticker": request.ticker})
    try:
        state = await workflow.run(request.ticker)
    except WorkflowTimeoutError as exc:
        limit = settings.agents.workflow_timeout_seconds
        raise AnalysisTimeoutError(
            f"The briefing did not complete within {limit:g} seconds."
        ) from exc

    if state.status == BriefingStatus.INSUFFICIENT_DATA:
        if state.ticker_not_found:
            raise TickerNotFoundError("financial_modeling_prep", request.ticker)
        raise InsufficientDataError(
            f"No market data, company profile or news could be retrieved for {request.ticker}.",
            details=[w.model_dump() for w in state.warnings],
        )

    logger.info(
        "briefing completed",
        extra={"ticker": request.ticker, "status": state.status, "warnings": len(state.warnings)},
    )
    return BriefingResponse.from_state(
        state, model=workflow.model_name, prompt_version=PROMPT_VERSION
    )
