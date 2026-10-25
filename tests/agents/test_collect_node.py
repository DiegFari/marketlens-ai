from __future__ import annotations

import pytest

from marketlens.agents.instrumentation import StepContext
from marketlens.agents.nodes.collect import collect_data
from marketlens.agents.state import BriefingState
from marketlens.services.errors import (
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    TickerNotFoundError,
)
from tests.agent_fakes import FakeMarketData, FakeNews, make_deps, make_feed, make_profile

pytestmark = pytest.mark.anyio


async def _collect(**deps_kwargs: object) -> dict[str, object]:
    deps = make_deps(**deps_kwargs)  # type: ignore[arg-type]
    return await collect_data(BriefingState(ticker="MSFT"), StepContext("collect_data"), deps)


def _codes(update: dict[str, object]) -> list[str]:
    return [w.code for w in update["warnings"]]  # type: ignore[attr-defined]


async def test_collects_snapshot_and_news_using_company_name() -> None:
    news = FakeNews()
    update = await _collect(news=news, news_lookback_days=5, news_articles_for_analysis=20)

    assert update["snapshot"] is not None
    assert update["news"] is not None
    assert news.calls == [("MSFT", "Microsoft Corporation", 5, 20)]
    assert _codes(update) == []


async def test_plan_restricted_ticker_falls_back_to_profile() -> None:
    market = FakeMarketData(
        error=ProviderPermissionError("fmp", "Special Endpoint"),
        profile=make_profile("ASML", "ASML Holding N.V."),
    )
    news = FakeNews()

    update = await _collect(market=market, news=news)

    assert update["snapshot"] is None
    assert update["profile"].name == "ASML Holding N.V."  # type: ignore[attr-defined]
    assert market.calls == ["snapshot", "profile"]
    assert news.calls[0][1] == "ASML Holding N.V."  # news still searched by name
    assert _codes(update) == ["not_in_data_plan"]


async def test_market_outage_still_collects_news_by_ticker() -> None:
    market = FakeMarketData(
        error=ProviderUnavailableError("fmp", "timeout"),
        profile_error=ProviderUnavailableError("fmp", "timeout"),
    )
    news = FakeNews()

    update = await _collect(market=market, news=news)

    assert update["profile"] is None
    assert news.calls[0][1] is None
    assert _codes(update) == ["market_data_unavailable", "profile_unavailable"]


async def test_unknown_ticker_is_flagged_and_news_not_searched() -> None:
    news = FakeNews()
    update = await _collect(
        market=FakeMarketData(error=TickerNotFoundError("fmp", "ZZZZ")), news=news
    )

    assert update["ticker_not_found"] is True
    assert news.calls == []


@pytest.mark.parametrize(
    ("news", "code"),
    [
        (FakeNews(ProviderRateLimitError("newsapi", "quota")), "news_unavailable"),
        (FakeNews(make_feed(count=0)), "no_recent_news"),
        (FakeNews(make_feed(broadened=True)), "thin_news_coverage"),
    ],
)
async def test_news_problems_become_warnings(news: FakeNews, code: str) -> None:
    update = await _collect(news=news)

    assert update["snapshot"] is not None
    assert _codes(update) == [code]


async def test_missing_providers_are_reported_not_fatal() -> None:
    update = await _collect(no_market=True, no_news=True)

    assert update["snapshot"] is None
    assert update["news"] is None
    assert _codes(update) == ["provider_not_configured", "provider_not_configured"]


async def test_snapshot_warnings_are_carried_as_info() -> None:
    from tests.agent_fakes import make_snapshot

    snapshot = make_snapshot().model_copy(update={"warnings": ["Financial metrics unavailable"]})
    update = await _collect(market=FakeMarketData(snapshot))

    warning = update["warnings"][0]  # type: ignore[index]
    assert (warning.code, warning.severity) == ("partial_market_data", "info")
