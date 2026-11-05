from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from marketlens.agents.graph import progress_events
from marketlens.agents.resolver import TickerResolution
from marketlens.agents.schemas import NewsAnalysis
from marketlens.agents.state import Step, StepStatus
from marketlens.api.main import create_app
from marketlens.core.config import DemoSettings, Settings
from marketlens.services.errors import TickerNotFoundError
from marketlens.services.registry import DataProviders
from tests.agent_fakes import NOW, FakeLLM, FakeMarketData, FakeNews, full_llm

STREAM = "/api/v1/briefings/stream"


def _client(settings: Settings, llm: FakeLLM | None, **providers: Any) -> TestClient:
    data = DataProviders(
        market_data=providers.get("market", FakeMarketData()),
        news=providers.get("news", FakeNews()),
    )
    return TestClient(create_app(settings, data, llm=llm))


def _events(client: TestClient, message: str) -> list[tuple[str, dict[str, Any]]]:
    response = client.post(STREAM, json={"message": message})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for block in response.text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if "event" in lines:
            events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_stream_reports_progress_then_the_briefing(settings: Settings) -> None:
    with _client(settings, full_llm()) as client:
        events = _events(client, "can you give me a brief on MSFT?")

    names = [name for name, _ in events]
    assert names[0] == "resolved"
    assert names[-1] == "briefing"
    assert events[0][1] == {"ticker": "MSFT", "company_name": None, "cached": False}

    steps = [(d["step"], d["status"]) for name, d in events if name == "step"]
    assert steps[0] == ("collect_data", "running")
    assert ("news_analyst", "succeeded") in steps
    assert steps[-1] == ("finalize", "succeeded")

    briefing = events[-1][1]
    assert briefing["ticker"] == "MSFT"
    assert briefing["status"] == "complete"
    assert [s["id"] for s in briefing["sources"]] == [1, 2]
    assert briefing["sources"][0]["url"] == "https://news.test/1"


def test_repeat_request_is_served_from_cache(settings: Settings) -> None:
    llm = full_llm()
    with _client(settings, llm) as client:
        _events(client, "MSFT")
        calls_after_first = len(llm.structured_calls)
        events = _events(client, "MSFT")

    assert events[0][1]["cached"] is True
    assert events[-1][1]["cached"] is True
    assert len(llm.structured_calls) == calls_after_first
    assert not [name for name, _ in events if name == "step"]


def test_company_name_is_resolved_by_the_model(settings: Settings) -> None:
    resolution = TickerResolution(
        status="resolved", ticker="MSFT", company_name="Microsoft", clarification=None
    )
    with _client(settings, full_llm(structured={TickerResolution: resolution})) as client:
        events = _events(client, "how is microsoft doing?")

    assert events[0] == (
        "resolved",
        {"ticker": "MSFT", "company_name": "Microsoft", "cached": False},
    )
    assert events[-1][0] == "briefing"


def test_unclear_request_gets_a_clarification(settings: Settings) -> None:
    resolution = TickerResolution(
        status="not_a_company", ticker=None, company_name=None, clarification="Which company?"
    )
    with _client(settings, full_llm(structured={TickerResolution: resolution})) as client:
        events = _events(client, "what's the weather?")

    assert events == [("clarification", {"message": "Which company?"})]


def test_unknown_ticker_is_an_error_event(settings: Settings) -> None:
    market = FakeMarketData(error=TickerNotFoundError("financial_modeling_prep", "ZZZZ"))
    with _client(settings, full_llm(), market=market) as client:
        events = _events(client, "ZZZZ")

    assert events[-1] == (
        "error",
        {"code": "ticker_not_found", "message": "No market data was found for this ticker."},
    )


def test_failed_step_details_are_not_exposed(settings: Settings) -> None:
    llm = full_llm(structured={NewsAnalysis: RuntimeError("429 org_secret rate limit")})
    with _client(settings, llm) as client:
        events = _events(client, "MSFT")

    text = json.dumps(events)
    assert "org_secret" not in text
    failed = [d for name, d in events if name == "step" and d["status"] == "failed"]
    assert failed
    assert failed[0]["detail"] == "This step failed; the briefing continues without it."
    assert events[-1][1]["status"] == "partial"


def test_client_limit_is_an_error_event(settings: Settings) -> None:
    limited = settings.model_copy(
        update={"demo": DemoSettings(limits_enabled=True, hourly_requests_per_client=1)}
    )
    with _client(limited, full_llm()) as client:
        _events(client, "MSFT")
        _events(client, "NVDA")  # different ticker: not cached, over the limit
        events = _events(client, "NVDA")

    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "client_limit_reached"


def test_stream_without_a_model_is_an_error_event(client: TestClient) -> None:
    events = _events(client, "MSFT")
    assert events == [("error", {"code": "llm_not_configured", "message": events[0][1]["message"]})]


def test_quota_reports_remaining_briefings(settings: Settings) -> None:
    limited = settings.model_copy(
        update={"demo": DemoSettings(limits_enabled=True, daily_briefings=3)}
    )
    with _client(limited, full_llm()) as client:
        before = client.get("/api/v1/briefings/quota").json()
        _events(client, "MSFT")
        after = client.get("/api/v1/briefings/quota").json()

    assert before == {"limits_enabled": True, "remaining_today": 3}
    assert after["remaining_today"] == 2


def test_examples_are_listed_and_served(client: TestClient) -> None:
    listing = client.get("/api/v1/examples").json()
    assert isinstance(listing, list)
    for summary in listing:
        example = client.get(f"/api/v1/examples/{summary['ticker'].lower()}")
        assert example.status_code == 200
        assert example.json()["ticker"] == summary["ticker"]

    missing = client.get("/api/v1/examples/NOPE")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "example_not_found"


def test_progress_events_from_graph_updates() -> None:
    from marketlens.agents.state import TraceEvent

    trace = TraceEvent(
        step="market_analyst", status=StepStatus.SUCCEEDED, started_at=NOW, duration_ms=12.5
    )
    events = progress_events(
        {
            "supervisor": {
                "next_steps": [Step.MARKET_ANALYST],
                "step_status": {Step.NEWS_ANALYST: StepStatus.SKIPPED},
            },
            "market_analyst": {"trace": [trace]},
        }
    )

    assert [(e.step, e.status) for e in events] == [
        ("news_analyst", "skipped"),
        ("market_analyst", "running"),
        ("market_analyst", "succeeded"),
    ]
    assert events[-1].duration_ms == 12.5
    assert progress_events(None) == []


def test_partial_briefings_are_not_cached(settings: Settings) -> None:
    llm = full_llm(structured={NewsAnalysis: RuntimeError("model down")})
    with _client(settings, llm) as client:
        first = _events(client, "MSFT")
        second = _events(client, "MSFT")

    assert first[-1][1]["status"] == "partial"
    assert second[0][1]["cached"] is False  # ran again instead of reusing the partial one


def test_no_agent_output_is_a_model_unavailable_error(settings: Settings) -> None:
    from marketlens.agents.schemas import ExecutiveBriefing, MarketAnalysis

    down = RuntimeError("429 tokens per day")
    llm = full_llm(structured={MarketAnalysis: down, NewsAnalysis: down, ExecutiveBriefing: down})
    with _client(settings, llm) as client:
        events = _events(client, "MSFT")

    assert events[-1][0] == "error"
    assert events[-1][1]["code"] == "model_unavailable"
