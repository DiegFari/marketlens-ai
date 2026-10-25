from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from marketlens.api.main import create_app
from marketlens.core.config import Settings
from marketlens.domain import (
    CompanyProfile,
    FinancialMetrics,
    MarketSnapshot,
    NewsArticle,
    NewsFeed,
    StockQuote,
)
from marketlens.services.errors import (
    DataProviderError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    TickerNotFoundError,
)
from marketlens.services.registry import DataProviders

NOW = datetime(2026, 10, 25, tzinfo=UTC)
PROFILE = CompanyProfile(symbol="MSFT", name="Microsoft Corporation", sector="Technology")
QUOTE = StockQuote(symbol="MSFT", price=512.34)


class FakeMarketData:
    def __init__(self, error: DataProviderError | None = None) -> None:
        self.error = error

    def _maybe_fail(self) -> None:
        if self.error is not None:
            raise self.error

    async def get_quote(self, symbol: str) -> StockQuote:
        self._maybe_fail()
        return QUOTE

    async def get_profile(self, symbol: str) -> CompanyProfile:
        self._maybe_fail()
        return PROFILE

    async def get_financial_metrics(self, symbol: str) -> FinancialMetrics:
        return FinancialMetrics(pe_ratio=37.6)

    async def get_snapshot(self, symbol: str) -> MarketSnapshot:
        self._maybe_fail()
        return MarketSnapshot(
            symbol=symbol,
            quote=QUOTE,
            profile=PROFILE,
            metrics=FinancialMetrics(pe_ratio=37.6),
            source="fake",
            retrieved_at=NOW,
        )

    async def aclose(self) -> None:
        pass


class FakeNews:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, int, int]] = []

    async def search_company_news(
        self, symbol: str, company_name: str | None, *, days: int = 7, limit: int = 20
    ) -> NewsFeed:
        self.calls.append((symbol, company_name, days, limit))
        article = NewsArticle(title="Headline", url="https://x.test/a", published_at=NOW)
        return NewsFeed(
            symbol=symbol,
            query=company_name or symbol,
            total_results=1,
            articles=[article],
            source="fake",
            retrieved_at=NOW,
        )

    async def aclose(self) -> None:
        pass


@contextmanager
def app_client(settings: Settings, providers: DataProviders) -> Iterator[TestClient]:
    with TestClient(create_app(settings, providers)) as client:
        yield client


def failing_market_data(settings: Settings, error: DataProviderError) -> TestClient:
    providers = DataProviders(market_data=FakeMarketData(error), news=FakeNews())
    return TestClient(create_app(settings, providers))


@pytest.fixture
def news() -> FakeNews:
    return FakeNews()


@pytest.fixture
def client(settings: Settings, news: FakeNews) -> Iterator[TestClient]:
    with app_client(settings, DataProviders(market_data=FakeMarketData(), news=news)) as client:
        yield client


def test_snapshot_returns_domain_model(client: TestClient) -> None:
    response = client.get("/api/v1/companies/msft/snapshot")

    body = response.json()
    assert response.status_code == 200
    assert body["symbol"] == "MSFT"  # normalised from the path
    assert body["quote"]["price"] == 512.34
    assert body["metrics"]["pe_ratio"] == 37.6


def test_invalid_ticker_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/companies/not a ticker/snapshot")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_unconfigured_provider_returns_503(settings: Settings) -> None:
    with app_client(settings, DataProviders()) as client:
        response = client.get("/api/v1/companies/MSFT/snapshot")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "provider_not_configured"


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (TickerNotFoundError("fmp", "ZZZZ"), 404, "ticker_not_found"),
        (ProviderUnavailableError("fmp", "timeout"), 503, "upstream_unavailable"),
        (ProviderRateLimitError("fmp", "Limit Reach"), 503, "upstream_rate_limited"),
        (ProviderPermissionError("fmp", "Premium plan only"), 502, "upstream_plan_restricted"),
        (DataProviderError("fmp", "weird"), 502, "upstream_error"),
    ],
)
def test_provider_errors_map_to_http(
    settings: Settings, error: DataProviderError, status: int, code: str
) -> None:
    with failing_market_data(settings, error) as client:
        response = client.get("/api/v1/companies/MSFT/snapshot")

    body = response.json()
    assert response.status_code == status
    assert body["error"]["code"] == code
    assert body["error"]["details"] == {"provider": "fmp"}
    assert error.message not in response.text  # provider wording stays in the logs


def test_rate_limit_propagates_retry_after(settings: Settings) -> None:
    error = ProviderRateLimitError("fmp", "slow down", retry_after=12.2)
    with failing_market_data(settings, error) as client:
        response = client.get("/api/v1/companies/MSFT/snapshot")

    assert response.headers["Retry-After"] == "13"


def test_news_searches_by_company_name(client: TestClient, news: FakeNews) -> None:
    response = client.get("/api/v1/companies/MSFT/news", params={"days": 3, "limit": 5})

    assert response.status_code == 200
    assert response.json()["articles"][0]["title"] == "Headline"
    assert news.calls == [("MSFT", "Microsoft Corporation", 3, 5)]


def test_news_falls_back_to_ticker_when_profile_lookup_fails(settings: Settings) -> None:
    news = FakeNews()
    market = FakeMarketData(ProviderUnavailableError("fmp", "down"))
    with app_client(settings, DataProviders(market_data=market, news=news)) as client:
        response = client.get("/api/v1/companies/MSFT/news")

    assert response.status_code == 200
    assert news.calls == [("MSFT", None, 7, 20)]


def test_news_returns_404_for_unknown_ticker(settings: Settings) -> None:
    with failing_market_data(settings, TickerNotFoundError("fmp", "ZZZZ")) as client:
        response = client.get("/api/v1/companies/ZZZZ/news")

    assert response.status_code == 404


def test_news_works_without_market_data_provider(settings: Settings) -> None:
    news = FakeNews()
    with app_client(settings, DataProviders(news=news)) as client:
        response = client.get("/api/v1/companies/MSFT/news")

    assert response.status_code == 200
    assert news.calls[0][1] is None


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 51}, {"days": 31}])
def test_news_validates_query_parameters(client: TestClient, params: dict[str, int]) -> None:
    response = client.get("/api/v1/companies/MSFT/news", params=params)

    assert response.status_code == 422
