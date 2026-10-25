from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from marketlens.services.base import RetryPolicy
from marketlens.services.errors import (
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderUnavailableError,
)
from marketlens.services.newsapi import NewsAPIClient, build_company_query
from tests.support import FakeSleep, MockAPI, load_fixture

pytestmark = pytest.mark.anyio

API_KEY = "news-secret-key-456"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


@pytest.fixture
def client(mock_api: MockAPI, fake_sleep: FakeSleep) -> NewsAPIClient:
    return NewsAPIClient(
        API_KEY,
        base_url="https://news.test",
        retry=RetryPolicy(max_retries=1),
        transport=mock_api.transport,
        sleep=fake_sleep,
    )


@pytest.mark.parametrize(
    ("symbol", "name", "expected"),
    [
        ("MSFT", "Microsoft Corporation", '"Microsoft" OR "MSFT"'),
        ("ASML.AS", "ASML Holding N.V.", '"ASML"'),
        ("BRK.B", "Berkshire Hathaway Inc.", '"Berkshire Hathaway" OR "BRK"'),
        ("F", "Ford Motor Company", '"Ford Motor"'),  # 1-letter ticker would match noise
        ("NVDA", None, '"NVDA"'),
        ("T", None, '"T"'),  # no name: the ticker is all we have
        ("XYZ", 'Quote "Problem" Inc', '"Quote Problem" OR "XYZ"'),
    ],
)
def test_build_company_query(symbol: str, name: str | None, expected: str) -> None:
    assert build_company_query(symbol, name) == expected


async def test_search_filters_dedupes_and_sorts(client: NewsAPIClient, mock_api: MockAPI) -> None:
    mock_api.add("/everything", httpx.Response(200, json=load_fixture("newsapi/everything.json")))

    feed = await client.search_company_news("MSFT", "Microsoft Corporation", now=NOW)

    titles = [a.title for a in feed.articles]
    assert titles == [
        "Microsoft beats quarterly revenue estimates on cloud demand",
        "Microsoft expands Azure AI capacity in Europe",
    ]
    assert feed.total_results == 6
    assert feed.query == '"Microsoft" OR "MSFT"'
    assert feed.articles[0].source == "Reuters"
    assert feed.articles[1].content_snippet == "Microsoft said on Monday it would add three regions"


async def test_respects_limit(client: NewsAPIClient, mock_api: MockAPI) -> None:
    mock_api.add("/everything", httpx.Response(200, json=load_fixture("newsapi/everything.json")))

    feed = await client.search_company_news("MSFT", "Microsoft", limit=1, now=NOW)

    assert len(feed.articles) == 1


async def test_request_parameters_and_header_auth(client: NewsAPIClient, mock_api: MockAPI) -> None:
    mock_api.add("/everything", httpx.Response(200, json={"status": "ok", "articles": []}))

    await client.search_company_news("MSFT", "Microsoft", days=3, limit=10, now=NOW)

    request = mock_api.requests[0]
    assert request.headers["X-Api-Key"] == API_KEY
    assert API_KEY not in str(request.url)
    assert request.url.params["from"] == "2026-10-22"
    assert request.url.params["pageSize"] == "20"
    assert request.url.params["sortBy"] == "publishedAt"
    assert request.url.params["language"] == "en"
    assert request.url.params["searchIn"] == "title"
    assert request.url.params["excludeDomains"] == "pypi.org,rlsbb.cc"


def _article(n: int, title: str) -> dict[str, object]:
    return {
        "source": {"name": f"Source {n}"},
        "title": title,
        "url": f"https://news.test/{n}",
        "publishedAt": f"2026-10-{10 + n:02d}T00:00:00Z",
    }


def _page(*articles: dict[str, object]) -> httpx.Response:
    body = {"status": "ok", "totalResults": len(articles), "articles": list(articles)}
    return httpx.Response(200, json=body)


async def test_enough_headline_matches_means_one_search(
    mock_api: MockAPI, fake_sleep: FakeSleep
) -> None:
    mock_api.add("/everything", _page(*(_article(n, f"Microsoft story {n}") for n in range(3))))
    client = NewsAPIClient(
        API_KEY,
        base_url="https://news.test",
        min_headline_articles=3,
        transport=mock_api.transport,
        sleep=fake_sleep,
    )

    feed = await client.search_company_news("MSFT", "Microsoft", limit=10, now=NOW)

    assert len(mock_api.requests) == 1
    assert feed.broadened is False
    assert len(feed.articles) == 3


async def test_thin_headline_coverage_is_topped_up_from_descriptions(
    mock_api: MockAPI, fake_sleep: FakeSleep
) -> None:
    headline = _article(1, "ASML raises guidance")
    mock_api.add(
        "/everything",
        _page(headline),
        _page(headline, _article(2, "Chip stocks fall"), _article(3, "Europe tech rallies")),
    )
    client = NewsAPIClient(
        API_KEY,
        base_url="https://news.test",
        min_headline_articles=10,
        transport=mock_api.transport,
        sleep=fake_sleep,
    )

    feed = await client.search_company_news("ASML", "ASML Holding N.V.", limit=2, now=NOW)

    assert [r.url.params["searchIn"] for r in mock_api.requests] == ["title", "title,description"]
    assert feed.broadened is True
    # The headline match always survives; the newest description match fills the gap.
    assert [a.title for a in feed.articles] == ["Europe tech rallies", "ASML raises guidance"]


@pytest.mark.parametrize(
    ("status", "code", "error_type"),
    [
        (401, "apiKeyInvalid", ProviderAuthError),
        (401, "apiKeyDisabled", ProviderAuthError),
        (429, "rateLimited", ProviderRateLimitError),
        (429, "apiKeyExhausted", ProviderRateLimitError),
        (400, "parameterInvalid", ProviderRequestError),
        (500, "unexpectedError", ProviderUnavailableError),
        (418, "someNewCode", ProviderRequestError),  # unknown code: fall back to HTTP status
    ],
)
async def test_error_codes_are_classified(
    client: NewsAPIClient,
    mock_api: MockAPI,
    status: int,
    code: str,
    error_type: type[Exception],
) -> None:
    body = {"status": "error", "code": code, "message": f"{code} happened"}
    mock_api.add("/everything", httpx.Response(status, json=body))

    with pytest.raises(error_type, match=f"{code} happened"):
        await client.search_company_news("MSFT", "Microsoft", now=NOW)


async def test_rate_limit_is_not_retried(
    client: NewsAPIClient, mock_api: MockAPI, fake_sleep: FakeSleep
) -> None:
    body = {"status": "error", "code": "rateLimited", "message": "slow down"}
    mock_api.add("/everything", httpx.Response(429, json=body))

    with pytest.raises(ProviderRateLimitError):
        await client.search_company_news("MSFT", "Microsoft", now=NOW)

    assert len(mock_api.requests) == 1
    assert fake_sleep.calls == []


@pytest.mark.parametrize(("days", "limit"), [(0, 10), (7, 0), (7, 101)])
async def test_rejects_invalid_arguments(client: NewsAPIClient, days: int, limit: int) -> None:
    with pytest.raises(ValueError, match="must be"):
        await client.search_company_news("MSFT", None, days=days, limit=limit)
