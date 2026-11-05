"""Executive briefing endpoint: runs the LangGraph multi-agent workflow for a ticker."""

from __future__ import annotations

from fastapi import APIRouter

from marketlens.agents.resolver import resolve_request
from marketlens.api.briefings_service import generate_briefing
from marketlens.api.dependencies import DemoGuardDep, LLMDep, SettingsDep, WorkflowDep
from marketlens.api.errors import LLMNotConfiguredApiError
from marketlens.api.schemas.analysis import (
    AnalysisRequest,
    BriefingResponse,
    ResolveRequest,
    ResolveResponse,
)
from marketlens.api.schemas.common import ErrorResponse

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.post(
    "",
    response_model=BriefingResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Ticker unknown to the data providers"},
        422: {"model": ErrorResponse, "description": "Invalid ticker, or no usable data"},
        429: {"model": ErrorResponse, "description": "Demo usage limit reached"},
        503: {"model": ErrorResponse, "description": "No language model configured, or busy"},
        504: {"model": ErrorResponse, "description": "Briefing took longer than the time limit"},
    },
)
async def create_analysis(
    request: AnalysisRequest, workflow: WorkflowDep, settings: SettingsDep, guard: DemoGuardDep
) -> BriefingResponse:
    """Generate an executive market briefing.

    Partial failures (a provider or an agent failing) still return 200 with
    ``status="partial"`` and structured ``warnings``; see the workflow docs.
    """
    # Checked here rather than in the dependency, so invalid input still gets a 422.
    if workflow is None:
        raise llm_not_configured(settings.llm.provider)
    return await generate_briefing(
        request.ticker,
        workflow=workflow,
        guard=guard,
        timeout_seconds=settings.agents.workflow_timeout_seconds,
    )


@router.post("/resolve", response_model=ResolveResponse)
async def resolve(request: ResolveRequest, llm: LLMDep) -> ResolveResponse:
    """Turn a free-text request ("brief on Amazon?") into a ticker, or a question back.

    A plain ticker is matched without a model call; anything else uses one short call.
    """
    resolved = await resolve_request(request.message, llm)
    return ResolveResponse(**resolved.model_dump())


def llm_not_configured(provider: str) -> LLMNotConfiguredApiError:
    return LLMNotConfiguredApiError(
        f"Briefings need a language model, but LLM_PROVIDER={provider} is "
        "not configured (e.g. set GROQ_API_KEY; see README)."
    )
