from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import httpx
import pytest

from marketlens.services.base import RetryPolicy
from marketlens.services.errors import (
    ProviderAuthError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderResponseError,
    TickerNotFoundError,
)
from marketlens.services.fmp import FMPClient
from tests.support import FakeSleep, MockAPI, load_fixture

pytestmark = pytest.mark.anyio

API_KEY = "fmp-secret-key-123"


@pytest.fixture
def client(mock_api: MockAPI, fake_sleep: FakeSleep) -> FMPClient:
    return FMPClient(
        API_KEY,
        base_url="https://fmp.test",
        retry=RetryPolicy(max_retries=1),
        transport=mock_api.transport,
        sleep=fake_sleep,
    )


def _json(name: str) -> httpx.Response:
    return httpx.Response(200, json=load_fixture(f"fmp/{name}.json"))


def _add_all(api: MockAPI) -> MockAPI:
    return (
        api.add("/quote", _json("quote"))
        .add("/profile", _json("profile"))
        .add("/ratios-ttm", _json("ratios_ttm"))
        .add("/key-metrics-ttm", _json("key_metrics_ttm"))
        .add("/financial-growth", _json("financial_growth"))
    )


async def test_get_quote_maps_fields(client: FMPClient, mock_api: MockAPI) -> None:
    mock_api.add("/quote", _json("quote"))

    quote = await client.get_quote("MSFT")

    assert quote.symbol == "MSFT"
    assert quote.price == 512.34
    assert quote.change_percent == 1.2345
    assert quote.avg_price_50d == 498.12
    assert quote.market_cap == 3_808_000_000_000
    assert quote.as_of == datetime(2025, 10, 24, 20, 0, tzinfo=UTC)


async def test_sends_symbol_and_api_key(client: FMPClient, mock_api: MockAPI) -> None:
    mock_api.add("/quote", _json("quote"))

    await client.get_quote("MSFT")

    request = mock_api.requests[0]
    assert request.url.params["symbol"] == "MSFT"
    assert request.headers["apikey"] == API_KEY
    assert API_KEY not in str(request.url)  # never in URLs, so never in URL logs


async def test_get_profile_coerces_provider_types(client: FMPClient, mock_api: MockAPI) -> None:
    mock_api.add("/profile", _json("profile"))

    profile = await client.get_profile("MSFT")

    assert profile.name == "Microsoft Corporation"
    assert profile.employees == 228_000  # sent as a string
    assert profile.ipo_date == date(1986, 3, 13)
    assert profile.sector == "Technology"


async def test_blank_strings_become_none(client: FMPClient, mock_api: MockAPI) -> None:
    raw = load_fixture("fmp/profile.json")
    raw[0].update({"beta": "", "ipoDate": "", "fullTimeEmployees": ""})
    mock_api.add("/profile", httpx.Response(200, json=raw))

    profile = await client.get_profile("MSFT")

    assert profile.beta is None
    assert profile.ipo_date is None
    assert profile.employees is None


async def test_empty_result_means_unknown_ticker(client: FMPClient, mock_api: MockAPI) -> None:
    mock_api.add("/quote", httpx.Response(200, json=[]))

    with pytest.raises(TickerNotFoundError) as exc_info:
        await client.get_quote("ZZZZ")

    assert exc_info.value.symbol == "ZZZZ"


@pytest.mark.parametrize("payload", [{"unexpected": "object"}, [{"symbol": "MSFT"}]])
async def test_malformed_payload_is_a_response_error(
    client: FMPClient, mock_api: MockAPI, payload: object
) -> None:
    mock_api.add("/quote", httpx.Response(200, json=payload))

    with pytest.raises(ProviderResponseError):
        await client.get_quote("MSFT")


async def test_financial_metrics_merge_both_endpoints(client: FMPClient, mock_api: MockAPI) -> None:
    _add_all(mock_api)

    metrics = await client.get_financial_metrics("MSFT")

    assert metrics.pe_ratio == 37.6
    assert metrics.gross_margin == 0.6876
    assert metrics.return_on_equity == 0.3296
    assert metrics.ev_to_ebitda == 24.8


async def test_revenue_growth_is_mapped_with_its_fiscal_period(
    client: FMPClient, mock_api: MockAPI
) -> None:
    _add_all(mock_api)

    metrics = await client.get_financial_metrics("MSFT")

    assert metrics.revenue_growth == pytest.approx(0.1779, abs=1e-4)
    assert metrics.revenue_growth_period == "FY2026"
    growth_call = next(r for r in mock_api.requests if r.url.path == "/financial-growth")
    assert growth_call.url.params["limit"] == "1"


@pytest.mark.parametrize(
    "growth_response",
    [
        httpx.Response(402, json={"Error Message": "Restricted Endpoint"}),
        httpx.Response(503),
        httpx.Response(200, json=[]),
    ],
)
async def test_missing_revenue_growth_keeps_other_metrics(
    client: FMPClient, mock_api: MockAPI, growth_response: httpx.Response
) -> None:
    _add_all(mock_api)
    mock_api.add("/financial-growth", growth_response)

    metrics = await client.get_financial_metrics("MSFT")

    assert metrics.revenue_growth is None
    assert metrics.revenue_growth_period is None
    assert metrics.pe_ratio == 37.6


async def test_financial_metrics_tolerate_empty_results(
    client: FMPClient, mock_api: MockAPI
) -> None:
    mock_api.add("/ratios-ttm", httpx.Response(200, json=[]))
    mock_api.add("/key-metrics-ttm", httpx.Response(200, json=[]))
    mock_api.add("/financial-growth", httpx.Response(200, json=[]))

    metrics = await client.get_financial_metrics("SPY")

    assert metrics.model_dump() == dict.fromkeys(metrics.model_dump())


async def test_snapshot_combines_all_sources(client: FMPClient, mock_api: MockAPI) -> None:
    _add_all(mock_api)

    snapshot = await client.get_snapshot("MSFT")

    assert snapshot.symbol == "MSFT"
    assert snapshot.profile.ceo == "Satya Nadella"
    assert snapshot.metrics is not None
    assert snapshot.source == "financial_modeling_prep"
    assert snapshot.warnings == []


async def test_snapshot_degrades_when_metrics_are_plan_restricted(
    client: FMPClient, mock_api: MockAPI
) -> None:
    _add_all(mock_api)
    restricted = httpx.Response(402, json={"Error Message": "Restricted Endpoint"})
    mock_api.add("/ratios-ttm", restricted)

    snapshot = await client.get_snapshot("MSFT")

    assert snapshot.metrics is None
    assert snapshot.warnings == ["Financial metrics unavailable: Restricted Endpoint"]


async def test_snapshot_fails_when_quote_is_missing(client: FMPClient, mock_api: MockAPI) -> None:
    _add_all(mock_api)
    mock_api.add("/quote", httpx.Response(200, json=[]))

    with pytest.raises(TickerNotFoundError):
        await client.get_snapshot("MSFT")


@pytest.mark.parametrize(
    ("status", "message", "error_type"),
    [
        (200, "Limit Reach . Please upgrade your plan", ProviderRateLimitError),
        (429, "Limit Reach . Please upgrade your plan", ProviderRateLimitError),
        (200, "Invalid API KEY. Feel free to create a Free API Key", ProviderAuthError),
        (401, "Invalid API KEY. Feel free to create a Free API Key", ProviderAuthError),
        (200, "Premium Query Parameter: this symbol requires a paid plan", ProviderPermissionError),
        (402, "Restricted Endpoint", ProviderPermissionError),
    ],
)
async def test_error_messages_are_classified(
    client: FMPClient,
    mock_api: MockAPI,
    status: int,
    message: str,
    error_type: type[Exception],
) -> None:
    mock_api.add("/quote", httpx.Response(status, json={"Error Message": message}))

    with pytest.raises(error_type) as exc_info:
        await client.get_quote("MSFT")

    assert str(exc_info.value).endswith(message)


async def test_api_key_never_appears_in_logs(
    client: FMPClient, mock_api: MockAPI, caplog: pytest.LogCaptureFixture
) -> None:
    _add_all(mock_api)
    mock_api.add("/ratios-ttm", httpx.Response(503), httpx.Response(503))

    with caplog.at_level(logging.DEBUG):
        await client.get_snapshot("MSFT")

    assert caplog.records  # sanity: something was logged
    assert all(API_KEY not in record.getMessage() for record in caplog.records)
    assert all(API_KEY not in str(record.__dict__) for record in caplog.records)
