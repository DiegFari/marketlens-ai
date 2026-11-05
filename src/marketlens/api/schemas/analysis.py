from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from marketlens.agents import BriefingState, BriefingStatus, TraceEvent, WorkflowWarning
from marketlens.agents.schemas import ExecutiveBriefing, MarketAnalysis, NewsAnalysis
from marketlens.domain import MarketSnapshot, Ticker


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"ticker": "MSFT"}]})

    ticker: Ticker = Field(description="Stock ticker symbol, e.g. AAPL, MSFT, NVDA.")


class ResolveRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"examples": [{"message": "can you give me a brief on AMZN?"}]}
    )

    message: str = Field(min_length=1, max_length=500, description="Free-text request")


class ResolveResponse(BaseModel):
    ticker: str | None = Field(description="The ticker to analyse, or null if unclear")
    company_name: str | None
    clarification: str | None = Field(description="What to ask the user when ticker is null")
    method: Literal["pattern", "llm"] = Field(description="How the ticker was found")


class NewsSource(BaseModel):
    """An article cited by the News agent; ``id`` matches ``NewsDevelopment.article_ids``."""

    id: int
    title: str
    url: str
    source: str | None
    published_at: datetime | None


class BriefingResponse(BaseModel):
    """The briefing as Markdown, plus the structured outputs behind it."""

    ticker: str
    company_name: str | None
    status: BriefingStatus = Field(description="complete, or partial with warnings")
    generated_at: datetime
    report_markdown: str = Field(description="The executive briefing, ready to display")
    warnings: list[WorkflowWarning]
    briefing: ExecutiveBriefing | None
    market_analysis: MarketAnalysis | None
    news_analysis: NewsAnalysis | None
    snapshot: MarketSnapshot | None
    trace: list[TraceEvent] = Field(description="Per-step execution trace")
    model: str = Field(description="Language model used")
    prompt_version: str
    sources: list[NewsSource] = Field(
        default_factory=list, description="Articles cited by the news developments"
    )
    cached: bool = Field(default=False, description="Served from a recent identical run")

    @classmethod
    def from_state(
        cls, state: BriefingState, *, model: str, prompt_version: str
    ) -> BriefingResponse:
        assert state.status is not None  # noqa: S101 (set by finalize or the runner)
        assert state.report_markdown is not None  # noqa: S101
        return cls(
            ticker=state.ticker,
            company_name=state.company_name,
            status=state.status,
            generated_at=state.generated_at or datetime.now().astimezone(),
            report_markdown=state.report_markdown,
            warnings=state.warnings,
            briefing=state.briefing,
            market_analysis=state.market_analysis,
            news_analysis=state.news_analysis,
            snapshot=state.snapshot,
            trace=state.trace,
            model=model,
            prompt_version=prompt_version,
            sources=_cited_sources(state),
        )


def _cited_sources(state: BriefingState) -> list[NewsSource]:
    if state.news is None or state.news_analysis is None:
        return []
    articles = state.news.articles
    cited = sorted(
        {
            article_id
            for dev in state.news_analysis.developments
            for article_id in dev.article_ids
            if 1 <= article_id <= len(articles)
        }
    )
    return [
        NewsSource(
            id=article_id,
            title=articles[article_id - 1].title,
            url=articles[article_id - 1].url,
            source=articles[article_id - 1].source,
            published_at=articles[article_id - 1].published_at,
        )
        for article_id in cited
    ]
