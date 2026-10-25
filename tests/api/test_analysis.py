from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from marketlens.agents.schemas import NewsAnalysis
from marketlens.api.main import create_app
from marketlens.api.schemas.analysis import AnalysisRequest
from marketlens.core.config import AgentSettings, Settings
from marketlens.services.errors import TickerNotFoundError
from marketlens.services.registry import DataProviders
from tests.agent_fakes import FakeLLM, FakeMarketData, FakeNews, full_llm


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("AAPL", "AAPL"), (" msft ", "MSFT"), ("brk.b", "BRK.B"), ("ASML.AS", "ASML.AS")],
)
def test_ticker_is_normalised(raw: str, expected: str) -> None:
    assert AnalysisRequest(ticker=raw).ticker == expected


@pytest.mark.parametrize("raw", ["", "123", "AAPL; DROP TABLE", "TOOLONGTICKER", "A..B"])
def test_invalid_ticker_is_rejected(client: TestClient, raw: str) -> None:
    response = client.post("/api/v1/analysis", json={"ticker": raw})

    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]


def _app_client(settings: Settings, llm: FakeLLM | None, **providers: object) -> TestClient:
    data = DataProviders(
        market_data=providers.get("market", FakeMarketData()),  # type: ignore[arg-type]
        news=providers.get("news", FakeNews()),  # type: ignore[arg-type]
    )
    return TestClient(create_app(settings, data, llm=llm))


def test_without_azure_openai_returns_503(client: TestClient) -> None:
    response = client.post("/api/v1/analysis", json={"ticker": "MSFT"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "llm_not_configured"


def test_briefing_is_returned_with_structured_outputs(settings: Settings) -> None:
    with _app_client(settings, full_llm()) as client:
        response = client.post("/api/v1/analysis", json={"ticker": "msft"})

    body = response.json()
    assert response.status_code == 200
    assert body["ticker"] == "MSFT"
    assert body["status"] == "complete"
    assert body["report_markdown"].startswith("# Executive Market Briefing: Microsoft")
    assert body["briefing"]["confidence"] == "high"
    assert body["snapshot"]["metrics"]["revenue_growth"] == 0.178
    assert body["model"] == "fake-gpt"
    assert body["prompt_version"]
    assert [e["step"] for e in body["trace"]][-1] == "finalize"


def test_partial_failure_is_200_with_warnings(settings: Settings) -> None:
    llm = full_llm(structured={NewsAnalysis: RuntimeError("model down")})
    with _app_client(settings, llm) as client:
        response = client.post("/api/v1/analysis", json={"ticker": "MSFT"})

    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "partial"
    assert body["news_analysis"] is None
    assert any(w["code"] == "news_analyst_failed" for w in body["warnings"])


def test_unknown_ticker_returns_404(settings: Settings) -> None:
    market = FakeMarketData(error=TickerNotFoundError("financial_modeling_prep", "ZZZZ"))
    with _app_client(settings, full_llm(), market=market) as client:
        response = client.post("/api/v1/analysis", json={"ticker": "ZZZZ"})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ticker_not_found"


def test_no_data_at_all_returns_422(settings: Settings) -> None:
    llm = full_llm()
    data = DataProviders(market_data=None, news=None)
    with TestClient(create_app(settings, data, llm=llm)) as client:
        response = client.post("/api/v1/analysis", json={"ticker": "MSFT"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "insufficient_data"


def test_timeout_returns_504(settings: Settings) -> None:
    class HangingLLM(FakeLLM):
        async def structured(self, messages, schema):  # type: ignore[no-untyped-def]
            await asyncio.sleep(10)

    slow_settings = settings.model_copy(
        update={"agents": AgentSettings(workflow_timeout_seconds=0.2)}
    )
    with _app_client(slow_settings, HangingLLM()) as client:
        response = client.post("/api/v1/analysis", json={"ticker": "MSFT"})

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "analysis_timeout"
