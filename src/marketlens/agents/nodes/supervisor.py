"""Supervisor: plans the workflow and reacts to what has happened so far.

After every step, control returns here. The supervisor reads the typed state (what data
exists, which steps succeeded, failed or were skipped) and decides what runs next:

    collect data ─► market + news analysts (in parallel, only those with input)
                 ─► report writer (if there is anything to report)
                 ─► finalize

Routing is a deterministic policy rather than an LLM call. The workflow's shape is
known; what varies is which inputs exist and which steps failed, which is policy, not
language. That keeps routing free, instant, auditable and exhaustively testable, and
spends model calls where judgment is needed (inside the agents).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from marketlens.agents.dependencies import AgentDependencies
from marketlens.agents.instrumentation import StepContext
from marketlens.agents.state import BriefingState, Step, StepStatus, WorkflowWarning

ANALYSTS = (Step.MARKET_ANALYST, Step.NEWS_ANALYST)


@dataclass(frozen=True)
class Decision:
    next_steps: list[Step]
    reason: str
    skipped: dict[Step, str] = field(default_factory=dict)
    turn_limit_hit: bool = False


def plan_next_steps(state: BriefingState, *, max_turns: int) -> Decision:
    done = state.step_status

    if state.supervisor_turns >= max_turns:
        return Decision([Step.FINALIZE], "turn limit reached", turn_limit_hit=True)

    if Step.COLLECT_DATA not in done:
        return Decision([Step.COLLECT_DATA], "collect market data and news")

    if state.ticker_not_found:
        return Decision([Step.FINALIZE], "ticker unknown to the market data provider")

    skipped: dict[Step, str] = {}
    analysts: list[Step] = []
    if Step.MARKET_ANALYST not in done:
        if state.snapshot is not None:
            analysts.append(Step.MARKET_ANALYST)
        else:
            skipped[Step.MARKET_ANALYST] = "no price or financial data"
    if Step.NEWS_ANALYST not in done:
        if state.has_articles:
            analysts.append(Step.NEWS_ANALYST)
        else:
            skipped[Step.NEWS_ANALYST] = "no news articles"
    if analysts:
        mode = "in parallel" if len(analysts) > 1 else "alone"
        return Decision(analysts, f"run {' + '.join(analysts)} {mode}", skipped)

    if Step.REPORT_WRITER not in done:
        if state.company_profile is not None or state.has_articles:
            return Decision([Step.REPORT_WRITER], "synthesise the briefing", skipped)
        skipped[Step.REPORT_WRITER] = "no data to report on"
        return Decision([Step.FINALIZE], "no usable data", skipped)

    return Decision([Step.FINALIZE], "all steps attempted", skipped)


async def supervisor(
    state: BriefingState, ctx: StepContext, deps: AgentDependencies
) -> dict[str, Any]:
    decision = plan_next_steps(state, max_turns=deps.settings.max_supervisor_turns)
    ctx.detail = f"→ {', '.join(decision.next_steps)} ({decision.reason})"
    if decision.skipped:
        ctx.detail += "; skipped " + ", ".join(f"{s} ({r})" for s, r in decision.skipped.items())

    update: dict[str, Any] = {
        "next_steps": decision.next_steps,
        "supervisor_turns": state.supervisor_turns + 1,
    }
    if decision.skipped:
        update["step_status"] = dict.fromkeys(decision.skipped, StepStatus.SKIPPED)
    if decision.turn_limit_hit:
        update["warnings"] = [
            WorkflowWarning(
                source="supervisor",
                code="turn_limit_reached",
                message="The workflow hit its step limit and was finalised early.",
            )
        ]
    return update
