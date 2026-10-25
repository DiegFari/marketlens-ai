from __future__ import annotations

import pytest

from marketlens.agents.rendering import determine_status, key_figures, render_markdown
from marketlens.agents.signals import derive_price_signals, market_facts
from marketlens.agents.state import BriefingState, BriefingStatus, Step, StepStatus, WorkflowWarning
from marketlens.domain import StockQuote
from tests.agent_fakes import (
    make_briefing,
    make_feed,
    make_market_analysis,
    make_news_analysis,
    make_profile,
    make_snapshot,
)

S, F, K = StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED
ALL_OK = {Step.MARKET_ANALYST: S, Step.NEWS_ANALYST: S, Step.REPORT_WRITER: S}


# ------------------------------------------------------------------ signals


def test_price_signals() -> None:
    quote = StockQuote(
        symbol="X",
        price=150,
        year_low=100,
        year_high=200,
        change_percent=2.5,
        avg_price_50d=140,
        avg_price_200d=120,
    )

    signals = derive_price_signals(quote)

    assert signals.position_in_52w_range == 0.5
    assert signals.percent_below_52w_high == 25.0
    assert signals.percent_vs_50d_avg == pytest.approx(7.14, abs=0.01)
    assert signals.trend == "uptrend"
    assert signals.day_change_percent == 2.5


@pytest.mark.parametrize(
    ("price", "ma50", "ma200", "trend"),
    [(90, 100, 110, "downtrend"), (105, 100, 110, "mixed"), (120, 110, 100, "uptrend")],
)
def test_trend_classification(price: float, ma50: float, ma200: float, trend: str) -> None:
    quote = StockQuote(symbol="X", price=price, avg_price_50d=ma50, avg_price_200d=ma200)
    assert derive_price_signals(quote).trend == trend


def test_signals_tolerate_missing_data() -> None:
    signals = derive_price_signals(StockQuote(symbol="X", price=10))
    assert signals.model_dump(exclude_none=True) == {}


def test_market_facts_are_compact() -> None:
    facts = market_facts(make_snapshot(metrics=False))
    assert "ttm_metrics" not in facts
    assert "description" not in str(facts)  # long text stays out of the prompt


# ------------------------------------------------------------------ figures and status


def test_key_figures_come_from_data() -> None:
    figures = dict(key_figures(make_snapshot()))

    assert figures["Share price"] == "$524.51 (+1.2% on the day)"
    assert figures["Market capitalisation"] == "$3.90T"
    assert figures["Revenue growth"] == "+17.8% (FY2026)"
    assert figures["P/E (TTM)"] == "29.1x"
    assert figures["Net margin (TTM)"] == "40.3%"
    assert "(uptrend)" in figures["vs 50 / 200-day average"]


def test_status_complete_partial_insufficient() -> None:
    full = BriefingState(ticker="MSFT", snapshot=make_snapshot(), step_status=ALL_OK)
    gap = WorkflowWarning(source="news", code="news_unavailable", message="x")
    info = WorkflowWarning(source="news", code="thin", message="x", severity="info")

    assert determine_status(full) == BriefingStatus.COMPLETE
    assert determine_status(full.model_copy(update={"warnings": [info]})) == BriefingStatus.COMPLETE
    assert determine_status(full.model_copy(update={"warnings": [gap]})) == BriefingStatus.PARTIAL
    assert (
        determine_status(full.model_copy(update={"step_status": {**ALL_OK, Step.NEWS_ANALYST: F}}))
        == BriefingStatus.PARTIAL
    )
    assert determine_status(BriefingState(ticker="X")) == BriefingStatus.INSUFFICIENT_DATA
    assert (
        determine_status(full.model_copy(update={"ticker_not_found": True}))
        == BriefingStatus.INSUFFICIENT_DATA
    )


# ------------------------------------------------------------------ markdown


def _complete_state() -> BriefingState:
    return BriefingState(
        ticker="MSFT",
        snapshot=make_snapshot(),
        news=make_feed(),
        market_analysis=make_market_analysis(),
        news_analysis=make_news_analysis(ids=[1, 2]),
        briefing=make_briefing(),
        step_status=ALL_OK,
        status=BriefingStatus.COMPLETE,
    )


def test_complete_briefing_has_all_sections_in_order() -> None:
    md = render_markdown(_complete_state())

    sections = [
        "# Executive Market Briefing: Microsoft Corporation (MSFT)",
        "## Company Summary",
        "## Recent Market Performance",
        "## Key News Developments",
        "## Bullish Signals",
        "## Risk Factors",
        "## Overall Assessment",
        "## Confidence & Data Notes",
    ]
    positions = [md.index(s) for s in sections]
    assert positions == sorted(positions)
    assert "| P/E (TTM) | 29.1x |" in md
    assert "[Reuters](https://news.test/1), [Bloomberg](https://news.test/2)" in md
    assert "Status: complete · Confidence: high" in md
    assert "Data notes" not in md
    assert "Not investment advice" in md


def test_warnings_are_surfaced_prominently() -> None:
    state = _complete_state().model_copy(
        update={
            "status": BriefingStatus.PARTIAL,
            "warnings": [
                WorkflowWarning(source="news", code="x", message="News search was unavailable."),
                WorkflowWarning(
                    source="news", code="y", message="Search widened.", severity="info"
                ),
            ],
        }
    )

    md = render_markdown(state)

    assert "> **Data notes**\n> - News search was unavailable." in md
    assert "- Search widened." in md.split("## Confidence & Data Notes")[1]


def test_report_failure_falls_back_to_structured_findings() -> None:
    state = _complete_state().model_copy(
        update={"briefing": None, "status": BriefingStatus.PARTIAL}
    )

    md = render_markdown(state)

    assert "Exceptional profitability: Net margin 40.3%" in md  # from market analysis
    assert "Premium valuation: P/E of 29.1x" in md
    assert "Cloud-driven earnings beat" in md  # positive development → bullish
    assert "could not be generated" in md
    assert "Confidence: low" in md


def test_profile_only_company_renders_without_figures() -> None:
    state = BriefingState(
        ticker="ASML",
        profile=make_profile("ASML", "ASML Holding N.V."),
        step_status={Step.MARKET_ANALYST: K, Step.NEWS_ANALYST: K},
        status=BriefingStatus.PARTIAL,
    )

    md = render_markdown(state)

    assert "ASML Holding N.V. (ASML)" in md
    assert "Market data is unavailable" in md
    assert "| Metric | Value |" not in md
    assert "No recent news coverage was found." in md


def test_insufficient_data_document() -> None:
    state = BriefingState(
        ticker="ZZZZ", ticker_not_found=True, status=BriefingStatus.INSUFFICIENT_DATA
    )

    md = render_markdown(state)

    assert "## Briefing unavailable" in md
    assert "was not recognised" in md
    assert "Confidence" not in md


def test_markdown_is_escaped_in_tables_and_links() -> None:
    feed = make_feed()
    article = feed.articles[0].model_copy(update={"source": "Odd [Source]"})
    state = _complete_state().model_copy(
        update={"news": feed.model_copy(update={"articles": [article, *feed.articles[1:]]})}
    )

    assert "[Odd (Source)](https://news.test/1)" in render_markdown(state)
