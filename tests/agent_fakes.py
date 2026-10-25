"""Scripted fakes for agent tests: an LLM, data providers and realistic sample data.

``FakeLLM`` satisfies ``LLMClient`` without any network access. Responses are scripted
per call type: ``chat`` turns (which may request tool calls) and ``structured`` outputs
keyed by schema. Every call is recorded for assertions.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from typing import Any, TypeVar

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel

from marketlens.agents import AgentDependencies
from marketlens.agents.schemas import (
    ExecutiveBriefing,
    FinancialSignal,
    MarketAnalysis,
    NewsAnalysis,
    NewsDevelopment,
)
from marketlens.core.config import AgentSettings
from marketlens.domain import (
    CompanyProfile,
    FinancialMetrics,
    MarketSnapshot,
    NewsArticle,
    NewsFeed,
    StockQuote,
)
from marketlens.llm import StructuredResult, TokenUsage
from marketlens.services.errors import DataProviderError

SchemaT = TypeVar("SchemaT", bound=BaseModel)
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

StructuredReply = BaseModel | Exception | Callable[[Sequence[BaseMessage]], BaseModel]


class FakeLLM:
    model_name = "fake-gpt"

    def __init__(
        self,
        *,
        chat: Sequence[AIMessage | Exception] = (),
        structured: dict[type[BaseModel], StructuredReply | list[StructuredReply]] | None = None,
    ) -> None:
        self._chat = deque(chat)
        self._structured = {
            schema: deque(r if isinstance(r, list) else [r])
            for schema, r in (structured or {}).items()
        }
        self.chat_calls: list[tuple[list[BaseMessage], list[str]]] = []
        self.structured_calls: list[tuple[list[BaseMessage], type[BaseModel]]] = []

    async def chat(
        self, messages: Sequence[BaseMessage], *, tools: Sequence[BaseTool] = ()
    ) -> AIMessage:
        self.chat_calls.append((list(messages), [t.name for t in tools]))
        if not self._chat:
            return AIMessage(content="Done.", usage_metadata=_usage(40, 5))
        reply = self._chat.popleft()
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def structured(
        self, messages: Sequence[BaseMessage], schema: type[SchemaT]
    ) -> StructuredResult[SchemaT]:
        self.structured_calls.append((list(messages), schema))
        queue = self._structured.get(schema)
        if not queue:
            raise AssertionError(f"FakeLLM has no scripted {schema.__name__}")
        reply = queue.popleft() if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        value = reply(messages) if callable(reply) and not isinstance(reply, BaseModel) else reply
        assert isinstance(value, schema)
        return StructuredResult(value, TokenUsage(input_tokens=500, output_tokens=200))

    def calls_for(self, schema: type[BaseModel]) -> list[list[BaseMessage]]:
        return [messages for messages, s in self.structured_calls if s is schema]


def _usage(inp: int, out: int) -> Any:
    return {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}


def tool_call(name: str, args: dict[str, Any], call_id: str = "call_1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}],
        usage_metadata=_usage(300, 20),
    )


# ------------------------------------------------------------------ sample data


def make_profile(symbol: str = "MSFT", name: str = "Microsoft Corporation") -> CompanyProfile:
    return CompanyProfile(
        symbol=symbol,
        name=name,
        description="Microsoft develops and supports software, services, devices and solutions. "
        "It operates through three segments.",
        sector="Technology",
        industry="Software - Infrastructure",
        country="US",
        exchange="NASDAQ",
        currency="USD",
        ceo="Satya Nadella",
        employees=228_000,
        ipo_date=date(1986, 3, 13),
        beta=1.04,
    )


def make_snapshot(symbol: str = "MSFT", *, metrics: bool = True) -> MarketSnapshot:
    quote = StockQuote(
        symbol=symbol,
        name="Microsoft Corporation",
        price=524.51,
        change=6.2,
        change_percent=1.2,
        year_low=344.79,
        year_high=555.45,
        volume=21_000_000,
        market_cap=3.895e12,
        avg_price_50d=505.0,
        avg_price_200d=460.0,
        exchange="NASDAQ",
        as_of=NOW,
    )
    return MarketSnapshot(
        symbol=symbol,
        quote=quote,
        profile=make_profile(symbol),
        metrics=FinancialMetrics(
            pe_ratio=29.1,
            gross_margin=0.69,
            operating_margin=0.45,
            net_margin=0.403,
            return_on_equity=0.33,
            debt_to_equity=0.3,
            revenue_growth=0.178,
            revenue_growth_period="FY2026",
        )
        if metrics
        else None,
        source="fake",
        retrieved_at=NOW,
    )


def make_feed(symbol: str = "MSFT", count: int = 4, *, broadened: bool = False) -> NewsFeed:
    titles = [
        "Microsoft beats revenue estimates on Azure demand",
        "Microsoft raises capital spending guidance for AI data centres",
        "Regulators open inquiry into Microsoft cloud licensing",
        "How to change your wallpaper in Windows",
        "Microsoft beats revenue estimates on Azure demand (syndicated)",
        "Microsoft signs nuclear power deal for data centres",
    ]
    articles = [
        NewsArticle(
            title=titles[i % len(titles)] + ("" if i < len(titles) else f" #{i}"),
            url=f"https://news.test/{i + 1}",
            source=["Reuters", "Bloomberg", "FT", "HowToGeek", "Syndicator", "CNBC"][i % 6],
            description=f"Description of article {i + 1}.",
            published_at=datetime(2026, 10, 24 - (i % 5), 9, 0, tzinfo=UTC),
        )
        for i in range(count)
    ]
    return NewsFeed(
        symbol=symbol,
        query='"Microsoft" OR "MSFT"',
        total_results=count,
        articles=articles,
        broadened=broadened,
        source="fake",
        retrieved_at=NOW,
    )


def make_market_analysis() -> MarketAnalysis:
    return MarketAnalysis(
        fundamentals_summary="Highly profitable with 40% net margins and 18% revenue growth.",
        strengths=[FinancialSignal(title="Exceptional profitability", evidence="Net margin 40.3%")],
        concerns=[FinancialSignal(title="Premium valuation", evidence="P/E of 29.1x")],
        valuation_view="Valuation is above the broad market, supported by growth.",
        confidence="high",
        data_gaps=[],
    )


def make_news_analysis(ids: Sequence[int] = (1, 2)) -> NewsAnalysis:
    return NewsAnalysis(
        summary="News flow is dominated by strong cloud results and rising AI investment.",
        developments=[
            NewsDevelopment(
                headline="Cloud-driven earnings beat",
                summary="Azure demand drove revenue above estimates.",
                sentiment="positive",
                importance="high",
                article_ids=list(ids),
            )
        ],
        overall_sentiment="positive",
        sentiment_confidence="medium",
        irrelevant_article_ids=[4],
        coverage_note="Coverage from major financial outlets over the past week.",
    )


def make_briefing() -> ExecutiveBriefing:
    return ExecutiveBriefing(
        company_summary="Microsoft is a leading global software and cloud provider.",
        market_performance="Shares trade near the top of their 52-week range, above both "
        "moving averages.",
        bullish_signals=["Azure growth", "Industry-leading margins", "Strong balance sheet"],
        risk_factors=["Premium valuation", "Regulatory scrutiny", "Rising capex"],
        overall_assessment="A high-quality franchise with strong momentum; valuation leaves "
        "little room for disappointment.",
        confidence="high",
        confidence_note="Complete market data and broad news coverage.",
    )


def full_llm(**overrides: Any) -> FakeLLM:
    """An LLM scripted for a successful end-to-end run (overridable per schema)."""
    structured: dict[type[BaseModel], Any] = {
        MarketAnalysis: make_market_analysis(),
        NewsAnalysis: make_news_analysis(),
        ExecutiveBriefing: make_briefing(),
    }
    structured.update(overrides.pop("structured", {}))
    return FakeLLM(structured=structured, **overrides)


# ------------------------------------------------------------------ providers


class FakeMarketData:
    def __init__(
        self,
        snapshot: MarketSnapshot | None = None,
        *,
        error: Exception | None = None,
        profile: CompanyProfile | None = None,
        profile_error: Exception | None = None,
    ) -> None:
        self.snapshot = snapshot or make_snapshot()
        self.error = error
        self.profile = profile or self.snapshot.profile
        self.profile_error = profile_error
        self.calls: list[str] = []

    async def get_snapshot(self, symbol: str) -> MarketSnapshot:
        self.calls.append("snapshot")
        if self.error:
            raise self.error
        return self.snapshot

    async def get_profile(self, symbol: str) -> CompanyProfile:
        self.calls.append("profile")
        if self.profile_error:
            raise self.profile_error
        return self.profile

    async def get_quote(self, symbol: str) -> StockQuote:
        return self.snapshot.quote

    async def get_financial_metrics(self, symbol: str) -> FinancialMetrics:
        return self.snapshot.metrics or FinancialMetrics()

    async def aclose(self) -> None:
        pass


class FakeNews:
    def __init__(self, *feeds: NewsFeed | DataProviderError) -> None:
        self._feeds = list(feeds) or [make_feed()]
        self.calls: list[tuple[str, str | None, int, int]] = []

    async def search_company_news(
        self, symbol: str, company_name: str | None, *, days: int = 7, limit: int = 20
    ) -> NewsFeed:
        self.calls.append((symbol, company_name, days, limit))
        item = self._feeds.pop(0) if len(self._feeds) > 1 else self._feeds[0]
        if isinstance(item, Exception):
            raise item
        return item

    async def aclose(self) -> None:
        pass


def make_deps(
    llm: FakeLLM | None = None,
    market: FakeMarketData | None = None,
    news: FakeNews | None = None,
    *,
    no_market: bool = False,
    no_news: bool = False,
    **settings: Any,
) -> AgentDependencies:
    return AgentDependencies(
        llm=llm or full_llm(),
        market_data=None if no_market else (market or FakeMarketData()),
        news=None if no_news else (news or FakeNews()),
        settings=AgentSettings(**settings),
    )
