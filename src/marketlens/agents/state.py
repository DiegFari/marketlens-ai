"""Typed state shared by every node of the briefing graph.

Nodes never mutate state: they return partial updates that LangGraph merges. Fields
written by parallel nodes have reducers (``warnings`` and ``trace`` append,
``step_status`` merges), so concurrent agents cannot overwrite each other.
"""

from __future__ import annotations

import operator
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from marketlens.agents.schemas import ExecutiveBriefing, MarketAnalysis, NewsAnalysis
from marketlens.domain import CompanyProfile, MarketSnapshot, NewsFeed

SUPERVISOR = "supervisor"


class Step(StrEnum):
    COLLECT_DATA = "collect_data"
    MARKET_ANALYST = "market_analyst"
    NEWS_ANALYST = "news_analyst"
    REPORT_WRITER = "report_writer"
    FINALIZE = "finalize"


class StepStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class BriefingStatus(StrEnum):
    COMPLETE = "complete"  # every agent succeeded and no data was missing
    PARTIAL = "partial"  # a briefing was produced, with documented gaps
    INSUFFICIENT_DATA = "insufficient_data"  # nothing meaningful to brief on


class WorkflowWarning(BaseModel):
    """A structured, user-facing note about missing or degraded data."""

    model_config = ConfigDict(frozen=True)

    source: str = Field(description="Step or provider that raised it")
    code: str = Field(description="Stable machine-readable code")
    message: str = Field(description="Plain-language explanation for the reader")
    severity: Literal["info", "warning"] = "warning"


class TraceEvent(BaseModel):
    """One node execution, for observability and the API's execution trace."""

    model_config = ConfigDict(frozen=True)

    step: str
    status: StepStatus
    started_at: datetime
    duration_ms: float
    detail: str | None = None
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[str] = Field(default_factory=list)
    # Structured outputs that needed the function-calling fallback (model quality signal).
    structured_fallbacks: int = 0


def merge_status(
    left: dict[Step, StepStatus], right: dict[Step, StepStatus]
) -> dict[Step, StepStatus]:
    return {**left, **right}


class BriefingState(BaseModel):
    ticker: str

    # Collected by `collect_data`
    snapshot: MarketSnapshot | None = None
    profile: CompanyProfile | None = None
    news: NewsFeed | None = None
    ticker_not_found: bool = False

    # Produced by the agents
    market_analysis: MarketAnalysis | None = None
    news_analysis: NewsAnalysis | None = None
    briefing: ExecutiveBriefing | None = None

    # Produced by `finalize`
    status: BriefingStatus | None = None
    report_markdown: str | None = None
    generated_at: datetime | None = None

    # Control flow and observability
    next_steps: list[Step] = Field(default_factory=list)
    supervisor_turns: int = 0
    step_status: Annotated[dict[Step, StepStatus], merge_status] = Field(default_factory=dict)
    warnings: Annotated[list[WorkflowWarning], operator.add] = Field(default_factory=list)
    trace: Annotated[list[TraceEvent], operator.add] = Field(default_factory=list)

    @property
    def company_profile(self) -> CompanyProfile | None:
        return self.snapshot.profile if self.snapshot else self.profile

    @property
    def company_name(self) -> str | None:
        profile = self.company_profile
        return profile.name if profile else None

    @property
    def has_articles(self) -> bool:
        return bool(self.news and self.news.articles)

    def status_of(self, step: Step) -> StepStatus | None:
        return self.step_status.get(step)
