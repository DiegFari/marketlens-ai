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
from collections.abc import Awaitable, Callable
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
from marketlens.agents.state import (
    SUPERVISOR,
    BriefingState,
    ProgressEvent,
    Step,
    StepStatus,
    TraceEvent,
)

logger = logging.getLogger("marketlens.agents")

ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]

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

    async def run(self, ticker: str, on_progress: ProgressCallback | None = None) -> BriefingState:
        """Run the graph. ``on_progress`` is told when steps start and finish (live UIs)."""
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
                    continue
                if self._deps.settings.debug:
                    logger.debug("graph update", extra={"update": _summarise(chunk)})
                if on_progress is not None:
                    for event in progress_events(chunk):
                        await on_progress(event)

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


def progress_events(update: Any) -> list[ProgressEvent]:
    """Translate one graph update into step started/finished events.

    The supervisor's plan says which steps start next (and which it skipped); a worker's
    trace entry says how it finished.
    """
    if not isinstance(update, dict):
        return []
    events: list[ProgressEvent] = []
    for node, value in update.items():
        if not isinstance(value, dict):
            continue
        if node == SUPERVISOR:
            for step, status in (value.get("step_status") or {}).items():
                events.append(ProgressEvent(step=Step(step).value, status=StepStatus(status)))
            for step in value.get("next_steps") or []:
                events.append(ProgressEvent(step=Step(step).value, status="running"))
            continue
        for raw in value.get("trace") or []:
            trace = raw if isinstance(raw, TraceEvent) else TraceEvent.model_validate(raw)
            events.append(
                ProgressEvent(
                    step=trace.step,
                    status=trace.status,
                    duration_ms=trace.duration_ms,
                    detail=trace.detail,
                    llm_calls=trace.llm_calls,
                    tool_calls=trace.tool_calls,
                )
            )
    return events


def _summarise(update: Any) -> dict[str, list[str]]:
    """Which keys each node wrote (values can be large; keys are what matter for flow)."""
    if not isinstance(update, dict):
        return {}
    return {
        node: sorted(value) if isinstance(value, dict) else [] for node, value in update.items()
    }
