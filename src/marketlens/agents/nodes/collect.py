"""Collect market data and news through the provider layer.

No LLM here: fetching is deterministic, cheap and must not depend on model behaviour.
Every provider failure becomes a structured warning, and the most useful fallback is
tried:

* plan-restricted ticker (e.g. ASML, SAP on FMP's free plan): fetch the profile alone
* market data unavailable: news is still searched (by ticker if no company name)
* unknown ticker: flagged, so the supervisor ends the run with "insufficient data"
"""

from __future__ import annotations

import logging
from typing import Any

from marketlens.agents.dependencies import AgentDependencies
from marketlens.agents.instrumentation import StepContext
from marketlens.agents.state import BriefingState, WorkflowWarning
from marketlens.domain import CompanyProfile, MarketSnapshot, NewsFeed
from marketlens.services.errors import (
    DataProviderError,
    ProviderPermissionError,
    TickerNotFoundError,
)

logger = logging.getLogger("marketlens.agents")

MARKET, NEWS = "market_data", "news"


async def collect_data(
    state: BriefingState, ctx: StepContext, deps: AgentDependencies
) -> dict[str, Any]:
    ticker = state.ticker
    warnings: list[WorkflowWarning] = []
    snapshot: MarketSnapshot | None = None
    profile: CompanyProfile | None = None
    not_found = False

    if deps.market_data is None:
        warnings.append(
            _warn(
                MARKET,
                "provider_not_configured",
                "Market data is not configured, so financial figures are unavailable.",
            )
        )
    else:
        try:
            snapshot = await deps.market_data.get_snapshot(ticker)
            profile = snapshot.profile
            warnings += [_warn(MARKET, "partial_market_data", w, "info") for w in snapshot.warnings]
        except TickerNotFoundError:
            not_found = True
        except ProviderPermissionError:
            warnings.append(
                _warn(
                    MARKET,
                    "not_in_data_plan",
                    f"Price and financial metrics for {ticker} are not included in the current "
                    "market data plan, which covers US listings. This briefing is based on the "
                    "company profile and news only.",
                )
            )
            profile = await _profile_fallback(deps, ticker, warnings)
        except DataProviderError as exc:
            warnings.append(
                _warn(
                    MARKET,
                    "market_data_unavailable",
                    f"Market data was temporarily unavailable ({type(exc).__name__}); "
                    "financial figures are missing from this briefing.",
                )
            )
            profile = await _profile_fallback(deps, ticker, warnings)

    feed = None if not_found else await _collect_news(deps, ticker, profile, warnings)

    ctx.detail = (
        f"snapshot={'yes' if snapshot else 'no'}, profile={'yes' if profile else 'no'}, "
        f"articles={len(feed.articles) if feed else 0}, ticker_not_found={not_found}"
    )
    return {
        "snapshot": snapshot,
        "profile": profile,
        "news": feed,
        "ticker_not_found": not_found,
        "warnings": warnings,
    }


async def _profile_fallback(
    deps: AgentDependencies, ticker: str, warnings: list[WorkflowWarning]
) -> CompanyProfile | None:
    assert deps.market_data is not None  # noqa: S101 (only called when configured)
    try:
        return await deps.market_data.get_profile(ticker)
    except DataProviderError as exc:
        logger.info(
            "profile fallback failed", extra={"ticker": ticker, "error": type(exc).__name__}
        )
        warnings.append(
            _warn(MARKET, "profile_unavailable", "The company profile is also unavailable.", "info")
        )
        return None


async def _collect_news(
    deps: AgentDependencies,
    ticker: str,
    profile: CompanyProfile | None,
    warnings: list[WorkflowWarning],
) -> NewsFeed | None:
    if deps.news is None:
        warnings.append(
            _warn(
                NEWS,
                "provider_not_configured",
                "News search is not configured, so recent developments are not covered.",
            )
        )
        return None
    try:
        feed = await deps.news.search_company_news(
            ticker,
            profile.name if profile else None,
            days=deps.settings.news_lookback_days,
            limit=deps.settings.news_articles_for_analysis,
        )
    except DataProviderError as exc:
        warnings.append(
            _warn(
                NEWS,
                "news_unavailable",
                f"News search was unavailable ({type(exc).__name__}); recent developments are "
                "not covered.",
            )
        )
        return None

    if not feed.articles:
        days = deps.settings.news_lookback_days
        warnings.append(
            _warn(NEWS, "no_recent_news", f"No news articles were found for the last {days} days.")
        )
    elif feed.broadened:
        warnings.append(
            _warn(
                NEWS,
                "thin_news_coverage",
                "Few headlines named the company, so the search was widened to article "
                "descriptions; some coverage may be indirect.",
                "info",
            )
        )
    return feed


def _warn(source: str, code: str, message: str, severity: str = "warning") -> WorkflowWarning:
    return WorkflowWarning.model_validate(
        {"source": source, "code": code, "message": message, "severity": severity}
    )
