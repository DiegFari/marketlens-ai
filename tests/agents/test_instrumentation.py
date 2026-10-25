from __future__ import annotations

import logging
from typing import Any

import pytest

from marketlens.agents.instrumentation import StepContext, instrument
from marketlens.agents.state import BriefingState, Step, StepStatus
from marketlens.llm import TokenUsage

pytestmark = pytest.mark.anyio


async def _ok(state: BriefingState, ctx: StepContext, deps: object) -> dict[str, Any]:
    ctx.record_llm(TokenUsage(100, 20))
    ctx.record_tool("read_articles")
    ctx.detail = "did things"
    return {"profile": None, "warnings": []}


async def _boom(state: BriefingState, ctx: StepContext, deps: object) -> dict[str, Any]:
    raise RuntimeError("model exploded")


async def test_success_is_traced_with_usage() -> None:
    node = instrument(Step.MARKET_ANALYST, _ok, None, failure_warning="unused")

    update = await node(BriefingState(ticker="MSFT"))

    event = update["trace"][0]
    assert update["step_status"] == {Step.MARKET_ANALYST: StepStatus.SUCCEEDED}
    assert (event.llm_calls, event.input_tokens, event.output_tokens) == (1, 100, 20)
    assert event.tool_calls == ["read_articles"]
    assert event.detail == "did things"
    assert update["warnings"] == []


async def test_failure_is_contained_and_reported() -> None:
    node = instrument(Step.NEWS_ANALYST, _boom, None, failure_warning="News unavailable.")

    update = await node(BriefingState(ticker="MSFT"))  # must not raise

    assert update["step_status"] == {Step.NEWS_ANALYST: StepStatus.FAILED}
    assert update["warnings"][0].message == "News unavailable."
    assert update["warnings"][0].code == "news_analyst_failed"
    assert update["trace"][0].detail == "RuntimeError: model exploded"


async def test_on_failure_update_is_applied() -> None:
    node = instrument(
        "supervisor",
        _boom,
        None,
        failure_warning="x",
        on_failure={"next_steps": [Step.FINALIZE]},
        track_status=False,
    )

    update = await node(BriefingState(ticker="MSFT"))

    assert update["next_steps"] == [Step.FINALIZE]
    assert "step_status" not in update


async def test_debug_mode_logs_prompts_and_outputs(caplog: pytest.LogCaptureFixture) -> None:
    from langchain_core.messages import HumanMessage

    ctx = StepContext("market_analyst", debug=True)
    with caplog.at_level(logging.DEBUG, logger="marketlens.agents"):
        ctx.log_prompt([HumanMessage("the prompt")])
        ctx.record_llm(TokenUsage(), output="the answer")

    messages = [r.getMessage() for r in caplog.records]
    assert messages == ["llm input", "llm output"]
    assert caplog.records[0].content == "the prompt"  # type: ignore[attr-defined]


async def test_prompts_not_logged_without_debug(caplog: pytest.LogCaptureFixture) -> None:
    from langchain_core.messages import HumanMessage

    with caplog.at_level(logging.DEBUG, logger="marketlens.agents"):
        StepContext("x").log_prompt([HumanMessage("secret-ish prompt")])

    assert caplog.records == []


def test_structured_fallbacks_are_counted() -> None:
    from pydantic import BaseModel

    from marketlens.llm import StructuredResult

    class Out(BaseModel):
        x: int

    ctx = StepContext("report_writer")
    ctx.record_structured(StructuredResult(Out(x=1), TokenUsage(10, 5), "json_schema"))
    ctx.record_structured(
        StructuredResult(Out(x=2), TokenUsage(10, 5), "function_calling", fallback_used=True)
    )

    assert (ctx.llm_calls, ctx.structured_fallbacks, ctx.usage.input_tokens) == (2, 1, 20)
