"""Report agent: synthesises the analysts' findings into an executive briefing.

It sees what the other agents produced, and is told explicitly what is missing and why,
so it can lower its confidence instead of papering over gaps. It writes narrative only;
figures and source links are rendered from data (rendering.py).
"""

from __future__ import annotations

import json
from typing import Any

from marketlens.agents.dependencies import AgentDependencies
from marketlens.agents.instrumentation import StepContext
from marketlens.agents.prompts import REPORT_WRITER_PROMPT
from marketlens.agents.rendering import key_figures
from marketlens.agents.schemas import ExecutiveBriefing
from marketlens.agents.state import BriefingState, Step, StepStatus

_UNAVAILABLE = {
    StepStatus.FAILED: "Unavailable: the agent failed during this run.",
    StepStatus.SKIPPED: "Unavailable: there was no input data for this agent.",
}


def _agent_output(state: BriefingState, step: Step, output: Any) -> str:
    if output is not None:
        return json.dumps(output.model_dump(), indent=1)
    return _UNAVAILABLE.get(state.status_of(step) or StepStatus.SKIPPED, "Unavailable.")


def _profile_text(state: BriefingState) -> str:
    profile = state.company_profile
    if profile is None:
        return "Unavailable."
    fields = {
        "name": profile.name,
        "sector": profile.sector,
        "industry": profile.industry,
        "country": profile.country,
        "employees": profile.employees,
        "description": (profile.description or "")[:1000] or None,
    }
    return json.dumps({k: v for k, v in fields.items() if v is not None}, indent=1)


async def report_writer(
    state: BriefingState, ctx: StepContext, deps: AgentDependencies
) -> dict[str, Any]:
    figures = key_figures(state.snapshot)
    news_output = state.news_analysis
    if news_output is not None:
        # Article ids mean nothing to the report writer; sources are rendered separately.
        news_output = news_output.model_copy(
            update={
                "developments": [
                    d.model_copy(update={"article_ids": []}) for d in news_output.developments
                ],
                "irrelevant_article_ids": [],
            }
        )

    messages = REPORT_WRITER_PROMPT.format_messages(
        company=state.company_name or state.ticker,
        ticker=state.ticker,
        profile=_profile_text(state),
        figures="\n".join(f"- {label}: {value}" for label, value in figures) or "Unavailable.",
        market_analysis=_agent_output(state, Step.MARKET_ANALYST, state.market_analysis),
        news_analysis=_agent_output(state, Step.NEWS_ANALYST, news_output),
        warnings="\n".join(f"- {w.message}" for w in state.warnings) or "- None",
    )
    ctx.log_prompt(messages)

    result = await deps.llm.structured(messages, ExecutiveBriefing)
    ctx.record_structured(result)
    ctx.detail = f"confidence={result.value.confidence}"
    return {"briefing": result.value}
