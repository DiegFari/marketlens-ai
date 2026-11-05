from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from marketlens.agents.resolver import TickerResolution, match_ticker, resolve_request
from marketlens.api.main import create_app
from marketlens.core.config import Settings
from marketlens.services.registry import DataProviders
from tests.agent_fakes import FakeLLM, FakeMarketData, FakeNews

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("AMZN", "AMZN"),
        ("  NVDA ", "NVDA"),
        ("$TSLA", "TSLA"),
        ("F", "F"),
        ("BRK.B", "BRK.B"),
        ("can you give me a brief on AMZN?", "AMZN"),
        ("I want the news on MSFT please", "MSFT"),
        ("What does the AI news say about NVDA in the US?", "NVDA"),
    ],
)
def test_obvious_tickers_need_no_model(message: str, expected: str) -> None:
    assert match_ticker(message) == expected


@pytest.mark.parametrize(
    "message",
    ["what's going on with amazon?", "amzn", "compare AAPL and MSFT", "Hi there", "I AM HERE"],
)
def test_unclear_messages_go_to_the_model(message: str) -> None:
    assert match_ticker(message) is None


def _resolution(**fields: object) -> TickerResolution:
    base: dict[str, object] = {
        "status": "resolved",
        "ticker": None,
        "company_name": None,
        "clarification": None,
    }
    return TickerResolution.model_validate(base | fields)


async def test_pattern_match_skips_the_model() -> None:
    llm = FakeLLM()

    resolved = await resolve_request("brief on AMZN", llm)

    assert (resolved.ticker, resolved.method) == ("AMZN", "pattern")
    assert llm.structured_calls == []


async def test_company_name_is_resolved_by_the_model() -> None:
    llm = FakeLLM(
        structured={TickerResolution: _resolution(ticker="amzn", company_name="Amazon.com")}
    )

    resolved = await resolve_request("what's going on with Amazon?", llm)

    assert (resolved.ticker, resolved.company_name, resolved.method) == (
        "AMZN",
        "Amazon.com",
        "llm",
    )
    assert "Amazon" in str(llm.structured_calls[0][0][-1].content)


async def test_ambiguous_request_returns_the_models_question() -> None:
    question = "Do you mean Apple (AAPL) or Applied Materials (AMAT)?"
    llm = FakeLLM(
        structured={TickerResolution: _resolution(status="ambiguous", clarification=question)}
    )

    resolved = await resolve_request("brief on apple-ish chips", llm)

    assert resolved.ticker is None
    assert resolved.clarification == question


async def test_invalid_model_ticker_is_rejected() -> None:
    llm = FakeLLM(structured={TickerResolution: _resolution(ticker="NOT A TICKER")})

    resolved = await resolve_request("brief on something", llm)

    assert resolved.ticker is None
    assert resolved.clarification


async def test_model_failure_degrades_to_a_hint() -> None:
    llm = FakeLLM(structured={TickerResolution: RuntimeError("model down")})

    resolved = await resolve_request("what about amazon", llm)

    assert resolved.ticker is None
    assert "AMZN" in (resolved.clarification or "")


async def test_without_a_model_only_tickers_work() -> None:
    resolved = await resolve_request("what about amazon", None)

    assert resolved.ticker is None
    assert resolved.clarification


def test_resolve_endpoint(settings: Settings) -> None:
    llm = FakeLLM(structured={TickerResolution: _resolution(ticker="NVDA", company_name="NVIDIA")})
    data = DataProviders(market_data=FakeMarketData(), news=FakeNews())  # type: ignore[arg-type]
    with TestClient(create_app(settings, data, llm=llm)) as client:
        by_pattern = client.post("/api/v1/analysis/resolve", json={"message": "brief on AMZN"})
        by_model = client.post("/api/v1/analysis/resolve", json={"message": "nvidia news?"})
        empty = client.post("/api/v1/analysis/resolve", json={"message": ""})

    assert by_pattern.json() == {
        "ticker": "AMZN",
        "company_name": None,
        "clarification": None,
        "method": "pattern",
    }
    assert by_model.json()["ticker"] == "NVDA"
    assert empty.status_code == 422
