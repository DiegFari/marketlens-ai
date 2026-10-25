"""A bounded tool-calling loop for agents.

The model is offered tools; when it requests calls, they run (concurrently) and their
results go back as tool messages, until the model stops asking or ``max_rounds`` is
reached. Tool failures and unknown tools become error *results* that the model can
react to; they never raise out of the loop.

Kept explicit rather than using a prebuilt ReAct agent so that round limits, error
handling, tracing and testing are fully under our control.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import BaseMessage, ToolCall, ToolMessage
from langchain_core.tools import BaseTool

from marketlens.agents.instrumentation import StepContext
from marketlens.llm import LLMClient

logger = logging.getLogger("marketlens.agents")


async def run_tool_loop(
    llm: LLMClient,
    messages: Sequence[BaseMessage],
    tools: Sequence[BaseTool],
    *,
    max_rounds: int,
    ctx: StepContext,
) -> list[BaseMessage]:
    """Return the full conversation, ending after the model's last turn or tool results."""
    by_name = {tool.name: tool for tool in tools}
    history = list(messages)
    ctx.log_prompt(history)

    for _ in range(max_rounds):
        reply = await llm.chat(history, tools=tools)
        ctx.record_chat(reply)
        history.append(reply)
        if not reply.tool_calls:
            return history
        history.extend(
            await asyncio.gather(*(_execute(by_name, call, ctx) for call in reply.tool_calls))
        )

    logger.info("tool round limit reached", extra={"step": ctx.step, "max_rounds": max_rounds})
    return history


async def _execute(by_name: dict[str, BaseTool], call: ToolCall, ctx: StepContext) -> ToolMessage:
    name, call_id = call["name"], call.get("id") or ""
    ctx.record_tool(name, call.get("args"))
    tool = by_name.get(name)
    if tool is None:
        content = f"ERROR: unknown tool '{name}'. Available: {', '.join(by_name)}"
    else:
        try:
            result: Any = await tool.ainvoke(call.get("args", {}))
            content = result if isinstance(result, str) else json.dumps(result, default=str)
        except Exception as exc:
            logger.warning("tool failed", extra={"tool": name, "error": type(exc).__name__})
            content = f"ERROR: {name} failed ({type(exc).__name__}: {exc})"
    return ToolMessage(content=content, tool_call_id=call_id, name=name)
