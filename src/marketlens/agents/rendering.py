"""Deterministic Markdown rendering of the briefing.

The Report agent writes the narrative; this module assembles the document around it.
Everything checkable comes from data, never from the model:

* the key-figures table is built from the market snapshot
* news source links are built from the article ids the News agent cited (verified)
* warnings come from the workflow state

When an agent failed, the corresponding section falls back to the best available
structured content, so a briefing is always produced.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from marketlens.agents.schemas import NewsDevelopment
from marketlens.agents.signals import derive_price_signals
from marketlens.agents.state import BriefingState, BriefingStatus, Step, StepStatus
from marketlens.domain import MarketSnapshot

DISCLAIMER = (
    "*Generated automatically from third-party data for research purposes. Not investment "
    "advice, and not a prediction of future prices. Verify material facts against primary "
    "sources.*"
)
_MAX_LINKS_PER_DEVELOPMENT = 3


# ------------------------------------------------------------------ formatting helpers


def _currency_prefix(currency: str | None) -> str:
    return "$" if currency in (None, "USD") else f"{currency} "


def _money(value: float, currency: str | None) -> str:
    return f"{_currency_prefix(currency)}{value:,.2f}"


def _big_money(value: float, currency: str | None) -> str:
    for threshold, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(value) >= threshold:
            return f"{_currency_prefix(currency)}{value / threshold:,.2f}{suffix}"
    return _money(value, currency)


def _signed_pct(value: float) -> str:
    return f"{value:+.1f}%"


def _ratio_pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _multiple(value: float) -> str:
    return f"{value:.1f}x"


def _two_dp(value: float) -> str:
    return f"{value:.2f}"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _link_text(text: str) -> str:
    return text.replace("[", "(").replace("]", ")")


# ------------------------------------------------------------------ figures


def key_figures(snapshot: MarketSnapshot | None) -> list[tuple[str, str]]:
    """Label/value rows for the figures table (and for the Report agent's prompt)."""
    if snapshot is None:
        return []
    quote, metrics, cur = snapshot.quote, snapshot.metrics, snapshot.profile.currency
    signals = derive_price_signals(quote)
    rows: list[tuple[str, str]] = []

    price = _money(quote.price, cur)
    if quote.change_percent is not None:
        price += f" ({_signed_pct(quote.change_percent)} on the day)"
    rows.append(("Share price", price))
    if quote.market_cap:
        rows.append(("Market capitalisation", _big_money(quote.market_cap, cur)))
    if quote.year_low is not None and quote.year_high is not None:
        value = f"{_money(quote.year_low, cur)} to {_money(quote.year_high, cur)}"
        if signals.position_in_52w_range is not None:
            value += f" (trading at {signals.position_in_52w_range:.0%} of range)"
        rows.append(("52-week range", value))
    ma50, ma200 = signals.percent_vs_50d_avg, signals.percent_vs_200d_avg
    if ma50 is not None and ma200 is not None:
        trend = f" ({signals.trend})" if signals.trend else ""
        rows.append(
            ("vs 50 / 200-day average", f"{_signed_pct(ma50)} / {_signed_pct(ma200)}{trend}")
        )

    if metrics is not None:
        if metrics.revenue_growth is not None:
            period = f" ({metrics.revenue_growth_period})" if metrics.revenue_growth_period else ""
            rows.append(("Revenue growth", _signed_pct(metrics.revenue_growth * 100) + period))
        optional: list[tuple[str, float | None, Callable[[float], str]]] = [
            ("P/E (TTM)", metrics.pe_ratio, _multiple),
            ("Gross margin (TTM)", metrics.gross_margin, _ratio_pct),
            ("Operating margin (TTM)", metrics.operating_margin, _ratio_pct),
            ("Net margin (TTM)", metrics.net_margin, _ratio_pct),
            ("Return on equity (TTM)", metrics.return_on_equity, _ratio_pct),
            ("Debt / equity", metrics.debt_to_equity, _two_dp),
            ("Free cash flow yield", metrics.free_cash_flow_yield, _ratio_pct),
            ("Dividend yield", metrics.dividend_yield, _ratio_pct),
        ]
        rows += [(label, fmt(value)) for label, value, fmt in optional if value is not None]
    return rows


# ------------------------------------------------------------------ status


def determine_status(state: BriefingState) -> BriefingStatus:
    if state.ticker_not_found or not (state.company_profile or state.has_articles):
        return BriefingStatus.INSUFFICIENT_DATA
    agents = (Step.MARKET_ANALYST, Step.NEWS_ANALYST, Step.REPORT_WRITER)
    all_succeeded = all(state.status_of(s) == StepStatus.SUCCEEDED for s in agents)
    has_gaps = any(w.severity == "warning" for w in state.warnings)
    return BriefingStatus.COMPLETE if all_succeeded and not has_gaps else BriefingStatus.PARTIAL


# ------------------------------------------------------------------ document


def render_markdown(state: BriefingState) -> str:
    generated_at = state.generated_at or datetime.now(UTC)
    status = state.status or determine_status(state)
    name = state.company_name or state.ticker
    confidence = state.briefing.confidence if state.briefing else "low"

    lines = [f"# Executive Market Briefing: {name} ({state.ticker})", ""]
    meta = f"*{generated_at:%d %B %Y, %H:%M} UTC · Status: {status.value.replace('_', ' ')}"
    if status != BriefingStatus.INSUFFICIENT_DATA:
        meta += f" · Confidence: {confidence}"
    lines += [meta + "*", ""]

    if status == BriefingStatus.INSUFFICIENT_DATA:
        return "\n".join(lines + _insufficient_data(state))

    material = [w for w in state.warnings if w.severity == "warning"]
    if material:
        lines.append("> **Data notes**")
        lines += [f"> - {w.message}" for w in material]
        lines.append("")

    lines += _company_summary(state)
    lines += _market_performance(state)
    lines += _news(state)
    lines += _signals(state, bullish=True)
    lines += _signals(state, bullish=False)
    lines += _assessment(state)
    lines += _confidence_and_sources(state, confidence)
    lines += ["---", "", DISCLAIMER, ""]
    return "\n".join(lines)


def _insufficient_data(state: BriefingState) -> list[str]:
    reason = (
        f"**{state.ticker}** was not recognised by the market data provider and no recent "
        "news was found."
        if state.ticker_not_found
        else f"No market data, company profile or news could be retrieved for **{state.ticker}**."
    )
    lines = ["## Briefing unavailable", "", reason, ""]
    if state.warnings:
        lines += ["**Details**", ""] + [f"- {w.message}" for w in state.warnings] + [""]
    lines += ["Check the ticker symbol. Coverage is best for US-listed companies.", ""]
    return lines


def _company_summary(state: BriefingState) -> list[str]:
    lines = ["## Company Summary", ""]
    profile = state.company_profile
    if state.briefing:
        lines.append(state.briefing.company_summary)
    elif profile and profile.description:
        lines.append(profile.description[:600].rsplit(". ", 1)[0] + ".")
    else:
        lines.append("_Company profile unavailable._")
    if profile:
        facts = [f for f in (profile.sector, profile.industry, profile.country) if f]
        if profile.employees:
            facts.append(f"{profile.employees:,} employees")
        if facts:
            lines += ["", f"*{' · '.join(facts)}*"]
    return [*lines, ""]


def _market_performance(state: BriefingState) -> list[str]:
    lines = ["## Recent Market Performance", ""]
    analysis = state.market_analysis
    if state.briefing:
        lines.append(state.briefing.market_performance)
    elif analysis:
        lines.append(analysis.fundamentals_summary)
    else:
        lines.append("_Market data is unavailable for this company._")
    if analysis:
        lines += ["", f"**Valuation.** {analysis.valuation_view}"]

    figures = key_figures(state.snapshot)
    if figures:
        lines += ["", "| Metric | Value |", "|---|---|"]
        lines += [f"| {_cell(label)} | {_cell(value)} |" for label, value in figures]
    return [*lines, ""]


def _news(state: BriefingState) -> list[str]:
    lines = ["## Key News Developments", ""]
    analysis = state.news_analysis
    if analysis is None:
        reason = {
            StepStatus.FAILED: "_News analysis could not be generated for this briefing._",
            StepStatus.SKIPPED: "_No recent news coverage was found._",
        }.get(state.status_of(Step.NEWS_ANALYST) or StepStatus.SKIPPED, "")
        return [*lines, reason or "_News analysis unavailable._", ""]

    lines += [analysis.summary, ""]
    if not analysis.developments:
        lines.append("_No developments material to investors were identified._")
    for dev in analysis.developments:
        lines.append(
            f"- **{dev.headline}**: {dev.summary} "
            f"*({dev.sentiment}, {dev.importance} importance)*{_sources(state, dev)}"
        )
    return [*lines, ""]


def _sources(state: BriefingState, dev: NewsDevelopment) -> str:
    if state.news is None:
        return ""
    links = []
    for article_id in dev.article_ids[:_MAX_LINKS_PER_DEVELOPMENT]:
        if 1 <= article_id <= len(state.news.articles):
            article = state.news.articles[article_id - 1]
            links.append(f"[{_link_text(article.source or 'source')}]({article.url})")
    return f" Sources: {', '.join(links)}" if links else ""


def _signals(state: BriefingState, *, bullish: bool) -> list[str]:
    title = "## Bullish Signals" if bullish else "## Risk Factors"
    if state.briefing:
        items = state.briefing.bullish_signals if bullish else state.briefing.risk_factors
    else:
        items = []
        if state.market_analysis:
            source = state.market_analysis.strengths if bullish else state.market_analysis.concerns
            items += [f"{s.title}: {s.evidence}" for s in source]
        if state.news_analysis:
            wanted = "positive" if bullish else "negative"
            items += [d.headline for d in state.news_analysis.developments if d.sentiment == wanted]
    body = [f"- {item}" for item in items] or ["_None identified from the available data._"]
    return [title, "", *body, ""]


def _assessment(state: BriefingState) -> list[str]:
    text = (
        state.briefing.overall_assessment
        if state.briefing
        else "_The narrative assessment could not be generated. The findings above are "
        "presented without synthesis._"
    )
    return ["## Overall Assessment", "", text, ""]


def _confidence_and_sources(state: BriefingState, confidence: str) -> list[str]:
    lines = ["## Confidence & Data Notes", "", f"**Confidence: {confidence}.**"]
    if state.briefing:
        lines[-1] += f" {state.briefing.confidence_note}"
    if state.market_analysis and state.market_analysis.data_gaps:
        lines += ["", "Data gaps: " + "; ".join(state.market_analysis.data_gaps) + "."]
    if state.news_analysis:
        lines += ["", f"News coverage: {state.news_analysis.coverage_note}"]
    notes = [w.message for w in state.warnings if w.severity == "info"]
    if notes:
        lines += ["", *[f"- {n}" for n in notes]]

    sources = []
    if state.snapshot:
        as_of = state.snapshot.quote.as_of
        sources.append(
            "market data from Financial Modeling Prep"
            + (f" (quote as of {as_of:%d %b %Y %H:%M} UTC)" if as_of else "")
        )
    if state.news:
        sources.append(f"news from NewsAPI ({len(state.news.articles)} articles screened)")
    if sources:
        lines += ["", "Sources: " + "; ".join(sources) + "."]
    return [*lines, ""]
