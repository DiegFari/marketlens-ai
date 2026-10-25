"""Market Data agent: interprets an existing ``MarketSnapshot``.

The model receives compact facts plus deterministic price signals (signals.py) and
returns a ``MarketAnalysis``: strengths and concerns, each tied to cited figures. It
has no tools: everything it needs is already in the snapshot, so a tool loop would only
add latency and cost.
"""

from __future__ import annotations

import json
from typing import Any

from marketlens.agents.dependencies import AgentDependencies
from marketlens.agents.instrumentation import StepContext
from marketlens.agents.prompts import MARKET_ANALYST_PROMPT
from marketlens.agents.schemas import MarketAnalysis
from marketlens.agents.signals import market_facts
from marketlens.agents.state import BriefingState


async def market_analyst(
    state: BriefingState, ctx: StepContext, deps: AgentDependencies
) -> dict[str, Any]:
    if state.snapshot is None:
        raise ValueError("market analyst requires a market snapshot")

    notes = [w.message for w in state.warnings if w.source == "market_data"]
    messages = MARKET_ANALYST_PROMPT.format_messages(
        company=state.snapshot.profile.name,
        ticker=state.ticker,
        facts=json.dumps(market_facts(state.snapshot), indent=1, default=str),
        data_notes="\n".join(f"- {n}" for n in notes) or "- None",
    )
    ctx.log_prompt(messages)

    result = await deps.llm.structured(messages, MarketAnalysis)
    ctx.record_structured(result)
    analysis = result.value
    ctx.detail = f"{len(analysis.strengths)} strengths, {len(analysis.concerns)} concerns"
    return {"market_analysis": analysis}
