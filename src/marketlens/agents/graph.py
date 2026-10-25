"""The LangGraph briefing workflow: a supervisor routing to specialist nodes.

    START ─► supervisor ─┬─► collect_data ───────────┐
               ▲         ├─► market_analyst ─┐ (parallel)
               │         ├─► news_analyst ───┤       │
               │         ├─► report_writer ──┤       │
               └─────────┴───────────────────┴───────┘
                         └─► finalize ─► END

Every worker returns to the supervisor, which re-plans from the updated state. Nodes
are wrapped by ``instrument()``, so a failing node yields a FAILED status and a warning,
never an exception; the supervisor routes around it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from marketlens.agents.dependencies import AgentDependencies
from marketlens.agents.instrumentation import instrument
from marketlens.agents.nodes.collect import collect_data
from marketlens.agents.nodes.finalize import finalize
from marketlens.agents.nodes.market import market_analyst
from marketlens.agents.nodes.news import news_analyst
from marketlens.agents.nodes.report import report_writer
from marketlens.agents.nodes.supervisor import supervisor
from marketlens.agents.rendering import determine_status, render_markdown
from marketlens.agents.state import SUPERVISOR, BriefingState, Step

logger = logging.getLogger("marketlens.agents")

WORKERS = (Step.COLLECT_DATA, Step.MARKET_ANALYST, Step.NEWS_ANALYST, Step.REPORT_WRITER)
# Each supervisor turn plus its workers is ~2 graph steps; a normal run needs ~10.
RECURSION_LIMIT = 50

FAILURE_WARNINGS = {
    Step.COLLECT_DATA: "Data collection failed unexpectedly; the briefing may be incomplete.",
    Step.MARKET_ANALYST: "The financial analysis could not be generated. Key figures are "
    "shown without interpretation.",
    Step.NEWS_ANALYST: "The news analysis could not be generated. Recent developments are not "
    "covered in this briefing.",
    Step.REPORT_WRITER: "The narrative briefing could not be generated. A structured summary "
    "of the available findings is shown instead.",
    Step.FINALIZE: "The briefing could not be formatted normally; a minimal version is shown.",
}


class WorkflowTimeoutError(Exception):
    pass


def route(state: BriefingState) -> list[str]:
    return [step.value for step in state.next_steps] or [Step.FINALIZE.value]


def build_graph(deps: AgentDependencies) -> CompiledStateGraph[Any, Any, Any, Any]:
    debug = deps.settings.debug
    graph = StateGraph(BriefingState)

    graph.add_node(
        SUPERVISOR,
        instrument(
            SUPERVISOR,
            supervisor,
            deps,
            failure_warning="Workflow planning failed; the briefing was finalised early.",
            # A broken supervisor must not loop: route straight to finalize.
            on_failure={"next_steps": [Step.FINALIZE]},
            track_status=False,
            debug=debug,
        ),
    )
    nodes = {
        Step.COLLECT_DATA: collect_data,
        Step.MARKET_ANALYST: market_analyst,
        Step.NEWS_ANALYST: news_analyst,
        Step.REPORT_WRITER: report_writer,
        Step.FINALIZE: finalize,
    }
    for step, node in nodes.items():
        graph.add_node(
            step.value,
            instrument(step, node, deps, failure_warning=FAILURE_WARNINGS[step], debug=debug),
        )

    graph.add_edge(START, SUPERVISOR)
    graph.add_conditional_edges(SUPERVISOR, route, [s.value for s in (*WORKERS, Step.FINALIZE)])
    for step in WORKERS:
        graph.add_edge(step.value, SUPERVISOR)
    graph.add_edge(Step.FINALIZE.value, END)
    return graph.compile()


class BriefingWorkflow:
    """Runs the graph for one ticker, with a time limit and a guaranteed document."""

    def __init__(self, deps: AgentDependencies) -> None:
        self._deps = deps
        self._graph = build_graph(deps)

    @property
    def model_name(self) -> str:
        return self._deps.llm.model_name

    async def run(self, ticker: str) -> BriefingState:
        final: Any = None

        async def drive() -> None:
            nonlocal final
            async for mode, chunk in self._graph.astream(
                BriefingState(ticker=ticker),
                stream_mode=["updates", "values"],
                config={"recursion_limit": RECURSION_LIMIT},
            ):
                if mode == "values":
                    final = chunk
                elif self._deps.settings.debug:
                    logger.debug("graph update", extra={"update": _summarise(chunk)})

        try:
            await asyncio.wait_for(drive(), self._deps.settings.workflow_timeout_seconds)
        except TimeoutError as exc:
            raise WorkflowTimeoutError(
                f"briefing for {ticker} exceeded {self._deps.settings.workflow_timeout_seconds}s"
            ) from exc

        state = BriefingState.model_validate(final)
        if state.report_markdown is None:  # finalize itself failed
            status = determine_status(state)
            state = state.model_copy(update={"status": status})
            state = state.model_copy(update={"report_markdown": render_markdown(state)})
        return state


def _summarise(update: Any) -> dict[str, list[str]]:
    """Which keys each node wrote (values can be large; keys are what matter for flow)."""
    if not isinstance(update, dict):
        return {}
    return {
        node: sorted(value) if isinstance(value, dict) else [] for node, value in update.items()
    }
