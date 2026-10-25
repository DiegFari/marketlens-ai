"""Structured outputs the agents ask the model for.

Constraints, because these are sent as strict JSON schemas to Azure OpenAI:
every field is required (no defaults), unknown fields are forbidden, and judgments are
enums rather than free numbers ("high"/"medium"/"low" is easier for a model to apply
consistently, and for a reader to trust, than 0.73).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Confidence = Literal["high", "medium", "low"]
Sentiment = Literal["positive", "negative", "neutral", "mixed"]
Importance = Literal["high", "medium", "low"]


class _LLMOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FinancialSignal(_LLMOutput):
    title: str = Field(description="Short label, e.g. 'Expanding operating margin'")
    evidence: str = Field(description="The specific figures from the data that support it")


class MarketAnalysis(_LLMOutput):
    """Output of the Market Data agent."""

    fundamentals_summary: str = Field(description="2-3 sentences on the financial profile")
    strengths: list[FinancialSignal] = Field(description="Up to 4, strongest first")
    concerns: list[FinancialSignal] = Field(description="Up to 4, most material first")
    valuation_view: str = Field(description="1-2 sentences; relative terms, no price targets")
    confidence: Confidence
    data_gaps: list[str] = Field(description="Missing data that limits the analysis")


class NewsDevelopment(_LLMOutput):
    headline: str = Field(description="Analyst-written one-line description of the development")
    summary: str = Field(description="1-2 sentences: what happened and why it matters")
    sentiment: Sentiment = Field(description="Likely read-through for the company")
    importance: Importance
    article_ids: list[int] = Field(description="Ids of every article covering this development")


class NewsAnalysis(_LLMOutput):
    """Output of the News Research agent."""

    summary: str = Field(description="2-3 sentences on the overall news picture")
    developments: list[NewsDevelopment] = Field(description="Up to 6, most important first")
    overall_sentiment: Sentiment
    sentiment_confidence: Confidence
    irrelevant_article_ids: list[int] = Field(description="Articles judged not about the company")
    coverage_note: str = Field(description="Caveats about coverage: volume, recency, sources")


class ExecutiveBriefing(_LLMOutput):
    """Output of the Report agent. Figures and sources are rendered from data, not here."""

    company_summary: str = Field(description="2-3 sentences: business and position")
    market_performance: str = Field(description="2-4 sentences interpreting recent trading")
    bullish_signals: list[str] = Field(description="3-5 concise bullets")
    risk_factors: list[str] = Field(description="3-5 concise bullets")
    overall_assessment: str = Field(description="3-5 sentences; balanced; no buy/sell advice")
    confidence: Confidence
    confidence_note: str = Field(description="Why this confidence level; name data gaps")
