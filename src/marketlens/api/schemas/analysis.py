from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from marketlens.agents import BriefingState, BriefingStatus, TraceEvent, WorkflowWarning
from marketlens.agents.schemas import ExecutiveBriefing, MarketAnalysis, NewsAnalysis
from marketlens.domain import MarketSnapshot, Ticker


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"ticker": "MSFT"}]})

    ticker: Ticker = Field(description="Stock ticker symbol, e.g. AAPL, MSFT, NVDA.")


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
    model: str = Field(description="Azure OpenAI deployment used")
    prompt_version: str

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
        )
