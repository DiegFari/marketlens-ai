"""Node-level tests for the three LLM agents, with a scripted model."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import ToolMessage

from marketlens.agents.instrumentation import StepContext
from marketlens.agents.nodes.market import market_analyst
from marketlens.agents.nodes.news import NewsWorkspace, news_analyst, verify_citations
from marketlens.agents.nodes.report import report_writer
from marketlens.agents.schemas import ExecutiveBriefing, MarketAnalysis, NewsAnalysis
from marketlens.agents.state import BriefingState, Step, StepStatus, WorkflowWarning
from tests.agent_fakes import (
    FakeLLM,
    FakeNews,
    full_llm,
    make_deps,
    make_feed,
    make_market_analysis,
    make_news_analysis,
    make_snapshot,
    tool_call,
)

pytestmark = pytest.mark.anyio


def _ctx(step: str = "test") -> StepContext:
    return StepContext(step)


# ------------------------------------------------------------------ market analyst


async def test_market_analyst_sends_facts_and_derived_signals() -> None:
    llm = full_llm()
    state = BriefingState(ticker="MSFT", snapshot=make_snapshot())
    ctx = _ctx()

    update = await market_analyst(state, ctx, make_deps(llm))

    assert update["market_analysis"] == make_market_analysis()
    prompt = llm.calls_for(MarketAnalysis)[0][-1].content
    assert '"pe_ratio": 29.1' in prompt
    assert '"revenue_growth_period": "FY2026"' in prompt
    assert '"trend": "uptrend"' in prompt  # computed, not left to the model
    assert (ctx.llm_calls, ctx.usage.input_tokens) == (1, 500)


async def test_market_analyst_passes_data_notes() -> None:
    llm = full_llm()
    warning = WorkflowWarning(source="market_data", code="x", message="Metrics plan-gated.")
    state = BriefingState(ticker="MSFT", snapshot=make_snapshot(metrics=False), warnings=[warning])

    await market_analyst(state, _ctx(), make_deps(llm))

    assert "Metrics plan-gated." in llm.calls_for(MarketAnalysis)[0][-1].content


async def test_market_analyst_requires_snapshot() -> None:
    with pytest.raises(ValueError, match="snapshot"):
        await market_analyst(BriefingState(ticker="MSFT"), _ctx(), make_deps())


# ------------------------------------------------------------------ news analyst


async def test_news_analyst_reads_articles_via_tool_then_analyses() -> None:
    llm = full_llm(chat=[tool_call("read_articles", {"article_ids": [1, 2, 99]})])
    state = BriefingState(ticker="MSFT", snapshot=make_snapshot(), news=make_feed())
    ctx = _ctx()

    update = await news_analyst(state, ctx, make_deps(llm))

    assert update["news_analysis"].developments[0].article_ids == [1, 2]
    assert ctx.tool_calls == ["read_articles"]
    # The model saw headlines first, then the tool result, then the final instruction.
    first_prompt = llm.chat_calls[0][0][-1].content
    assert "[1] 2026-10-24 | Reuters | Microsoft beats revenue estimates" in first_prompt
    assert llm.chat_calls[0][1] == ["read_articles", "search_older_news"]
    # The final call gets research as plain notes: no tool-call history, which strict
    # providers (Groq) reject when the tools are not offered in that request.
    final = llm.calls_for(NewsAnalysis)[0]
    assert not any(isinstance(m, ToolMessage) or getattr(m, "tool_calls", None) for m in final)
    notes = str(final[-2].content)
    payload = json.loads(notes.split("[read_articles] ", 1)[1])
    assert [a["id"] for a in payload["articles"]] == [1, 2]
    assert payload["unknown_ids"] == [99]


async def test_news_analyst_can_widen_search_once() -> None:
    older = make_feed(count=6)
    news = FakeNews(older)
    llm = full_llm(
        chat=[
            tool_call("search_older_news", {"days": 21}, "c1"),
            tool_call("search_older_news", {"days": 30}, "c2"),
        ]
    )
    state = BriefingState(ticker="MSFT", news=make_feed(count=2))

    update = await news_analyst(state, _ctx(), make_deps(llm, news=news))

    assert news.calls == [("MSFT", None, 21, 30)]  # second widening refused
    assert len(update["news"].articles) == 6  # 2 originals + 4 new (de-duplicated)
    second_result = llm.chat_calls[2][0][-1]
    assert "already been widened" in str(second_result.content)


async def test_news_analyst_drops_invented_citations() -> None:
    hallucinated = make_news_analysis(ids=[42, 43])
    llm = full_llm(structured={NewsAnalysis: hallucinated})
    state = BriefingState(ticker="MSFT", news=make_feed(count=3))

    update = await news_analyst(state, _ctx(), make_deps(llm))

    assert update["news_analysis"].developments == []
    assert update["warnings"][0].code == "unsourced_developments_removed"


async def test_news_tool_failure_is_returned_to_the_model_not_raised() -> None:
    from marketlens.services.errors import ProviderUnavailableError

    news = FakeNews(ProviderUnavailableError("newsapi", "down"))
    llm = full_llm(chat=[tool_call("search_older_news", {"days": 14})])
    state = BriefingState(ticker="MSFT", news=make_feed(count=2))

    update = await news_analyst(state, _ctx(), make_deps(llm, news=news))

    assert update["news_analysis"] is not None
    assert "ERROR: search_older_news failed" in str(llm.chat_calls[1][0][-1].content)


async def test_news_analyst_respects_tool_round_limit() -> None:
    llm = full_llm(
        chat=[tool_call("read_articles", {"article_ids": [1]}, f"c{i}") for i in range(9)]
    )
    state = BriefingState(ticker="MSFT", news=make_feed())
    ctx = _ctx()

    await news_analyst(state, ctx, make_deps(llm, news_max_tool_rounds=2))

    assert len(llm.chat_calls) == 2
    assert ctx.tool_calls == ["read_articles", "read_articles"]


def test_verify_citations_dedupes_and_filters_ids() -> None:
    analysis = make_news_analysis(ids=[2, 2, 7, 1])

    cleaned, dropped = verify_citations(analysis, article_count=3)

    assert cleaned.developments[0].article_ids == [2, 1]
    assert cleaned.irrelevant_article_ids == []  # id 4 is out of range for 3 articles
    assert dropped == 0


def test_workspace_assigns_stable_ids_and_dedupes() -> None:
    feed = make_feed(count=3)
    workspace = NewsWorkspace(feed.articles)

    start = workspace.add([*feed.articles[:2], *make_feed(count=5).articles[3:]])

    assert start == 3
    assert len(workspace.articles) == 5
    assert workspace.get(4) is not None
    assert workspace.get(6) is None


# ------------------------------------------------------------------ report writer


async def test_report_writer_receives_all_inputs() -> None:
    llm = full_llm()
    state = BriefingState(
        ticker="MSFT",
        snapshot=make_snapshot(),
        market_analysis=make_market_analysis(),
        news_analysis=make_news_analysis(),
    )

    update = await report_writer(state, _ctx(), make_deps(llm))

    assert isinstance(update["briefing"], ExecutiveBriefing)
    prompt = llm.calls_for(ExecutiveBriefing)[0][-1].content
    assert "Revenue growth: +17.8% (FY2026)" in prompt
    assert "Exceptional profitability" in prompt
    assert "Cloud-driven earnings beat" in prompt
    assert '"article_ids": []' in prompt  # ids are stripped; links are rendered from data


async def test_report_writer_is_told_why_inputs_are_missing() -> None:
    llm = full_llm()
    state = BriefingState(
        ticker="MSFT",
        snapshot=make_snapshot(),
        market_analysis=make_market_analysis(),
        step_status={Step.NEWS_ANALYST: StepStatus.FAILED},
        warnings=[WorkflowWarning(source="news_analyst", code="x", message="News failed.")],
    )

    await report_writer(state, _ctx(), make_deps(llm))

    prompt = llm.calls_for(ExecutiveBriefing)[0][-1].content
    assert "Unavailable: the agent failed during this run." in prompt
    assert "- News failed." in prompt


async def test_llm_errors_propagate_to_the_instrumentation_layer() -> None:
    llm = FakeLLM(structured={MarketAnalysis: RuntimeError("Azure 500")})
    state = BriefingState(ticker="MSFT", snapshot=make_snapshot())

    with pytest.raises(RuntimeError, match="Azure 500"):
        await market_analyst(state, _ctx(), make_deps(llm))
