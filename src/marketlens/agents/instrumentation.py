"""Failure isolation, tracing and debug logging for graph nodes.

Every node runs inside ``instrument()``. It times the node, records LLM usage and tool
calls on a ``StepContext``, appends a ``TraceEvent``, and, crucially, **converts any
exception into a FAILED status plus a user-facing warning instead of raising**. One
agent failing therefore never crashes the workflow; the supervisor sees the status and
routes around it.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar

from langchain_core.messages import AIMessage, BaseMessage

from marketlens.agents.state import BriefingState, StepStatus, TraceEvent, WorkflowWarning
from marketlens.llm import StructuredResult, TokenUsage

logger = logging.getLogger("marketlens.agents")

DepsT = TypeVar("DepsT")
NodeFn = Callable[[BriefingState, "StepContext", DepsT], Awaitable[dict[str, Any]]]


class GraphNode(Protocol):
    """The node shape LangGraph expects (the parameter must be named ``state``)."""

    def __call__(self, state: BriefingState) -> Awaitable[dict[str, Any]]: ...


class StepContext:
    """Per-execution scratchpad a node uses to report what it did."""

    def __init__(self, step: str, *, debug: bool = False) -> None:
        self.step = step
        self.debug = debug
        self.llm_calls = 0
        self.usage = TokenUsage()
        self.tool_calls: list[str] = []
        self.structured_fallbacks = 0
        self.detail: str | None = None

    def record_llm(self, usage: TokenUsage, *, output: object = None) -> None:
        self.llm_calls += 1
        self.usage = self.usage + usage
        if self.debug:
            logger.debug("llm output", extra={"step": self.step, "output": _preview(output)})

    def record_structured(self, result: StructuredResult[Any]) -> None:
        self.record_llm(result.usage, output=result.value)
        if result.fallback_used:
            self.structured_fallbacks += 1

    def record_chat(self, message: AIMessage) -> None:
        self.record_llm(
            TokenUsage.from_message(message), output=message.content or message.tool_calls
        )

    def record_tool(self, name: str, args: object = None) -> None:
        self.tool_calls.append(name)
        logger.info("tool call", extra={"step": self.step, "tool": name})
        if self.debug:
            logger.debug("tool args", extra={"step": self.step, "tool": name, "args": args})

    def log_prompt(self, messages: Sequence[BaseMessage]) -> None:
        if self.debug:
            for message in messages:
                logger.debug(
                    "llm input",
                    extra={
                        "step": self.step,
                        "role": message.type,
                        "content": _preview(message.content),
                    },
                )


def _preview(value: object, limit: int = 2000) -> str:
    text = str(value)
    return text if len(text) <= limit else f"{text[:limit]}… [+{len(text) - limit} chars]"


def instrument(
    step: str,
    node: NodeFn[DepsT],
    deps: DepsT,
    *,
    failure_warning: str,
    on_failure: dict[str, Any] | None = None,
    track_status: bool = True,
    debug: bool = False,
) -> GraphNode:
    """Wrap ``node`` so it is traced, logged and can never raise into the graph.

    ``on_failure`` is merged into the update when the node fails, e.g. so a failing
    supervisor still routes to ``finalize`` instead of looping. ``track_status=False``
    is for the supervisor, which is a router rather than a workflow step.
    """

    async def run(state: BriefingState) -> dict[str, Any]:
        ctx = StepContext(step, debug=debug)
        started_at = datetime.now(UTC)
        start = time.perf_counter()
        logger.info("step started", extra={"step": step, "ticker": state.ticker})

        warnings: list[WorkflowWarning] = []
        try:
            update = await node(state, ctx, deps)
            status = StepStatus.SUCCEEDED
        except Exception as exc:
            logger.exception("step failed", extra={"step": step, "error": type(exc).__name__})
            update = dict(on_failure or {})
            status = StepStatus.FAILED
            ctx.detail = f"{type(exc).__name__}: {exc}"[:300]
            warnings.append(
                WorkflowWarning(source=step, code=f"{step}_failed", message=failure_warning)
            )

        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        event = TraceEvent(
            step=step,
            status=status,
            started_at=started_at,
            duration_ms=duration_ms,
            detail=ctx.detail,
            llm_calls=ctx.llm_calls,
            input_tokens=ctx.usage.input_tokens,
            output_tokens=ctx.usage.output_tokens,
            tool_calls=ctx.tool_calls,
            structured_fallbacks=ctx.structured_fallbacks,
        )
        logger.info(
            "step finished",
            extra={
                "step": step,
                "status": status.value,
                "duration_ms": duration_ms,
                "llm_calls": ctx.llm_calls,
                "tool_calls": len(ctx.tool_calls),
            },
        )
        result = {
            **update,
            "warnings": [*update.get("warnings", []), *warnings],
            "trace": [*update.get("trace", []), event],
        }
        if track_status:
            result["step_status"] = {**update.get("step_status", {}), step: status}
        return result

    run.__name__ = f"{step}_node"
    return run
