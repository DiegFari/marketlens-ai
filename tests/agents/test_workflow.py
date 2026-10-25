"""End-to-end LangGraph runs with a scripted model and fake providers.

Covers the happy path and each degradation path the workflow promises to survive.
"""

from __future__ import annotations

import asyncio

import pytest

from marketlens.agents import (
    BriefingStatus,
    BriefingWorkflow,
    Step,
    StepStatus,
    WorkflowTimeoutError,
)
from marketlens.agents.schemas import ExecutiveBriefing, MarketAnalysis, NewsAnalysis
from marketlens.llm import LLMOutputError
from marketlens.services.errors import (
    ProviderPermissionError,
    ProviderUnavailableError,
    TickerNotFoundError,
)
from tests.agent_fakes import (
    FakeLLM,
    FakeMarketData,
    FakeNews,
    full_llm,
    make_briefing,
    make_deps,
    make_feed,
    make_market_analysis,
    make_news_analysis,
    make_profile,
    tool_call,
)

pytestmark = pytest.mark.anyio


async def _run(ticker: str = "MSFT", **deps_kwargs: object) -> object:
    return await BriefingWorkflow(make_deps(**deps_kwargs)).run(ticker)  # type: ignore[arg-type]


def _statuses(state: object) -> dict[Step, StepStatus]:
    return dict(state.step_status)  # type: ignore[attr-defined]


def _codes(state: object) -> list[str]:
    return [w.code for w in state.warnings]  # type: ignore[attr-defined]


async def test_happy_path_produces_complete_briefing() -> None:
    llm = full_llm(chat=[tool_call("read_articles", {"article_ids": [1, 2]})])

    state = await _run(llm=llm)

    assert state.status == BriefingStatus.COMPLETE  # type: ignore[attr-defined]
    assert _statuses(state) == dict.fromkeys(Step, StepStatus.SUCCEEDED)
    assert state.warnings == []  # type: ignore[attr-defined]
    md = state.report_markdown  # type: ignore[attr-defined]
    assert "## Overall Assessment" in md
    assert "| P/E (TTM) | 29.1x |" in md
    # 3 structured outputs + 2 chat turns (tool call, then done)
    assert len(llm.structured_calls) == 3
    assert len(llm.chat_calls) == 2


async def test_trace_records_every_step_in_order() -> None:
    state = await _run()

    steps = [e.step for e in state.trace]  # type: ignore[attr-defined]
    assert steps[0] == "supervisor"
    assert steps[1] == "collect_data"
    assert set(steps[3:5]) == {"market_analyst", "news_analyst"}  # parallel pair
    assert steps[-2:] == ["supervisor", "finalize"]
    news = next(e for e in state.trace if e.step == "news_analyst")  # type: ignore[attr-defined]
    assert news.llm_calls == 2
    assert news.input_tokens > 0


async def test_analysts_run_concurrently() -> None:
    started: list[str] = []
    both_started = asyncio.Event()

    class SlowLLM(FakeLLM):
        async def structured(self, messages, schema):  # type: ignore[no-untyped-def]
            if schema in (MarketAnalysis, NewsAnalysis):
                started.append(schema.__name__)
                if len(started) == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), timeout=2)  # deadlocks if sequential
            return await super().structured(messages, schema)

    llm = SlowLLM(
        structured={
            MarketAnalysis: make_market_analysis(),
            NewsAnalysis: make_news_analysis(),
            ExecutiveBriefing: make_briefing(),
        }
    )

    state = await _run(llm=llm)

    assert state.status == BriefingStatus.COMPLETE  # type: ignore[attr-defined]


async def test_news_agent_failure_still_produces_briefing_with_warning() -> None:
    llm = full_llm(structured={NewsAnalysis: LLMOutputError("refusal")})

    state = await _run(llm=llm)

    assert state.status == BriefingStatus.PARTIAL  # type: ignore[attr-defined]
    assert _statuses(state)[Step.NEWS_ANALYST] == StepStatus.FAILED
    assert _statuses(state)[Step.REPORT_WRITER] == StepStatus.SUCCEEDED
    assert "news_analyst_failed" in _codes(state)
    md = state.report_markdown  # type: ignore[attr-defined]
    assert "News analysis could not be generated" in md
    assert "Data notes" in md
    report_prompt = llm.calls_for(ExecutiveBriefing)[0][-1].content
    assert "the agent failed during this run" in report_prompt


async def test_market_agent_failure_keeps_figures_table() -> None:
    state = await _run(llm=full_llm(structured={MarketAnalysis: RuntimeError("timeout")}))

    assert _statuses(state)[Step.MARKET_ANALYST] == StepStatus.FAILED
    assert "| Share price |" in state.report_markdown  # type: ignore[attr-defined]


async def test_report_agent_failure_yields_structured_fallback() -> None:
    state = await _run(llm=full_llm(structured={ExecutiveBriefing: RuntimeError("boom")}))

    assert state.status == BriefingStatus.PARTIAL  # type: ignore[attr-defined]
    assert state.briefing is None  # type: ignore[attr-defined]
    md = state.report_markdown  # type: ignore[attr-defined]
    assert "Exceptional profitability" in md
    assert "narrative assessment could not be generated" in md


async def test_every_llm_call_failing_still_returns_a_document() -> None:
    llm = FakeLLM(
        chat=[RuntimeError("down")] * 3,
        structured={
            s: RuntimeError("down") for s in (MarketAnalysis, NewsAnalysis, ExecutiveBriefing)
        },
    )

    state = await _run(llm=llm)

    assert state.status == BriefingStatus.PARTIAL  # type: ignore[attr-defined]
    assert "| Share price | $524.51" in state.report_markdown  # type: ignore[attr-defined]
    assert {"market_analyst_failed", "news_analyst_failed", "report_writer_failed"} <= set(
        _codes(state)
    )


async def test_plan_restricted_company_degrades_to_profile_and_news() -> None:
    market = FakeMarketData(
        error=ProviderPermissionError("fmp", "Special Endpoint"),
        profile=make_profile("ASML", "ASML Holding N.V."),
    )
    llm = full_llm()

    state = await _run("ASML", llm=llm, market=market)

    assert state.status == BriefingStatus.PARTIAL  # type: ignore[attr-defined]
    assert _statuses(state)[Step.MARKET_ANALYST] == StepStatus.SKIPPED
    assert _statuses(state)[Step.REPORT_WRITER] == StepStatus.SUCCEEDED
    assert "not_in_data_plan" in _codes(state)
    assert llm.calls_for(MarketAnalysis) == []  # no wasted model call
    assert "ASML Holding N.V. (ASML)" in state.report_markdown  # type: ignore[attr-defined]


async def test_news_outage_skips_news_agent() -> None:
    state = await _run(news=FakeNews(ProviderUnavailableError("newsapi", "down")))

    assert _statuses(state)[Step.NEWS_ANALYST] == StepStatus.SKIPPED
    assert "news_unavailable" in _codes(state)
    assert state.briefing is not None  # type: ignore[attr-defined]


async def test_unknown_ticker_is_insufficient_data_without_llm_calls() -> None:
    llm = full_llm()

    state = await _run(
        "ZZZZ", llm=llm, market=FakeMarketData(error=TickerNotFoundError("fmp", "ZZZZ"))
    )

    assert state.status == BriefingStatus.INSUFFICIENT_DATA  # type: ignore[attr-defined]
    assert state.ticker_not_found  # type: ignore[attr-defined]
    assert llm.structured_calls == []
    assert llm.chat_calls == []


async def test_no_providers_at_all_is_insufficient_data() -> None:
    state = await _run(no_market=True, no_news=True)

    assert state.status == BriefingStatus.INSUFFICIENT_DATA  # type: ignore[attr-defined]
    assert "## Briefing unavailable" in state.report_markdown  # type: ignore[attr-defined]


async def test_news_only_when_market_provider_missing() -> None:
    state = await _run(no_market=True, news=FakeNews(make_feed()))

    assert _statuses(state)[Step.MARKET_ANALYST] == StepStatus.SKIPPED
    assert _statuses(state)[Step.NEWS_ANALYST] == StepStatus.SUCCEEDED
    assert state.status == BriefingStatus.PARTIAL  # type: ignore[attr-defined]


async def test_workflow_timeout() -> None:
    class HangingLLM(FakeLLM):
        async def structured(self, messages, schema):  # type: ignore[no-untyped-def]
            await asyncio.sleep(10)

    with pytest.raises(WorkflowTimeoutError):
        await _run(llm=HangingLLM(), workflow_timeout_seconds=0.2)


async def test_supervisor_turn_limit_terminates_workflow() -> None:
    state = await _run(max_supervisor_turns=3)

    assert state.report_markdown  # type: ignore[attr-defined]
    assert "turn_limit_reached" in _codes(state)


async def test_finalize_failure_still_returns_a_document(monkeypatch: pytest.MonkeyPatch) -> None:
    import marketlens.agents.nodes.finalize as finalize_module

    def broken(*_: object, **__: object) -> str:
        raise RuntimeError("template bug")

    monkeypatch.setattr(finalize_module, "render_markdown", broken)

    state = await _run()

    assert state.report_markdown.startswith("# Executive Market Briefing")  # type: ignore[attr-defined]
    assert "finalize_failed" in _codes(state)


async def test_debug_mode_logs_graph_updates_and_prompts(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.DEBUG, logger="marketlens.agents"):
        await _run(debug=True)

    messages = {r.getMessage() for r in caplog.records}
    assert {"graph update", "llm input", "llm output", "step started", "step finished"} <= messages
