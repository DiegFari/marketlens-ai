"""News Research agent: filters, de-duplicates and interprets an existing ``NewsFeed``.

This is the agent that genuinely needs tools. It starts from a list of headlines only
(cheap, and enough to discard noise), then decides for itself:

* ``read_articles``: which articles are worth reading in detail
* ``search_older_news``: whether coverage is too thin and the window should widen

Real-data probing showed why this matters: even after the provider's keyword tuning, feeds
contain same-name noise ("cane SAP" is a sugar price), consumer reviews and syndicated
duplicates. Telling those apart takes judgment, not keywords.

Citations are verified after the model answers: developments may only cite article ids
that exist, so every source link in the briefing is real.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from marketlens.agents.dependencies import AgentDependencies
from marketlens.agents.instrumentation import StepContext
from marketlens.agents.prompts import (
    NEWS_ANALYST_FINAL_INSTRUCTION,
    NEWS_ANALYST_FINAL_SYSTEM,
    NEWS_ANALYST_PROMPT,
)
from marketlens.agents.schemas import NewsAnalysis
from marketlens.agents.state import BriefingState, Step, WorkflowWarning
from marketlens.agents.tool_loop import run_tool_loop
from marketlens.domain import NewsArticle
from marketlens.services.protocols import NewsProvider

MAX_READ_PER_CALL = 10
MAX_WIDENED_DAYS = 30


class NewsWorkspace:
    """The articles the agent is working with. Ids are 1-based positions."""

    def __init__(self, articles: Sequence[NewsArticle]) -> None:
        self.articles = list(articles)
        self.widened = False

    def get(self, article_id: int) -> NewsArticle | None:
        return self.articles[article_id - 1] if 1 <= article_id <= len(self.articles) else None

    def headlines(self, start: int = 0) -> str:
        return "\n".join(
            f"[{i}] {a.published_at:%Y-%m-%d} | {a.source or 'unknown'} | {a.title}"
            for i, a in enumerate(self.articles[start:], start=start + 1)
        )

    def add(self, articles: Sequence[NewsArticle]) -> int:
        """Append unseen articles; return the index where the new ones start."""
        start = len(self.articles)
        seen = {a.url for a in self.articles} | {a.title.casefold() for a in self.articles}
        for article in articles:
            if article.url not in seen and article.title.casefold() not in seen:
                self.articles.append(article)
                seen |= {article.url, article.title.casefold()}
        return start


class ReadArticlesInput(BaseModel):
    article_ids: list[int] = Field(
        description=f"Ids from the headline list (max {MAX_READ_PER_CALL})"
    )


class SearchOlderNewsInput(BaseModel):
    days: int = Field(description=f"New look-back window in days, up to {MAX_WIDENED_DAYS}")


def build_news_tools(
    workspace: NewsWorkspace,
    provider: NewsProvider | None,
    *,
    ticker: str,
    company_name: str | None,
    limit: int,
) -> list[BaseTool]:
    async def read_articles(article_ids: list[int]) -> str:
        found, unknown = [], []
        for article_id in article_ids[:MAX_READ_PER_CALL]:
            article = workspace.get(article_id)
            if article is None:
                unknown.append(article_id)
                continue
            found.append(
                {
                    "id": article_id,
                    "title": article.title,
                    "source": article.source,
                    "published_at": article.published_at.isoformat(),
                    "description": article.description,
                    "content_snippet": article.content_snippet,
                }
            )
        return json.dumps({"articles": found, "unknown_ids": unknown})

    async def search_older_news(days: int) -> str:
        if workspace.widened:
            return "ERROR: the search window has already been widened once."
        if provider is None:
            return "ERROR: news search is not available."
        workspace.widened = True
        feed = await provider.search_company_news(
            ticker, company_name, days=max(8, min(MAX_WIDENED_DAYS, days)), limit=limit
        )
        start = workspace.add(feed.articles)
        if start == len(workspace.articles):
            return "No additional articles were found."
        return "Additional headlines:\n" + workspace.headlines(start)

    tools: list[BaseTool] = [
        StructuredTool.from_function(
            coroutine=read_articles,
            name="read_articles",
            description="Read the description and opening text of articles by id.",
            args_schema=ReadArticlesInput,
        )
    ]
    if provider is not None:
        tools.append(
            StructuredTool.from_function(
                coroutine=search_older_news,
                name="search_older_news",
                description="Widen the news search to an older time window. Use at most once, "
                "only when fewer than 3 relevant articles are available.",
                args_schema=SearchOlderNewsInput,
            )
        )
    return tools


def research_summary(
    history: Sequence[BaseMessage], *, company: str, ticker: str
) -> list[BaseMessage]:
    """Turn the tool-calling conversation into plain notes for the final answer.

    Found in the first real runs on Groq: given the raw tool-call history, or the
    research prompt that tells it to call ``read_articles``, the model sometimes calls
    the tool again during the structured-output step, and strict providers reject calls
    to tools not offered in that request. So the final step gets a tool-free system
    prompt, the headlines, and the tool results as plain notes, which works everywhere.
    """
    headlines = next(m for m in history if isinstance(m, HumanMessage))
    notes = [f"[{m.name}] {m.content}" for m in history if isinstance(m, ToolMessage) and m.content]
    research = "\n\n".join(notes) or "No articles were read in detail."
    return [
        SystemMessage(NEWS_ANALYST_FINAL_SYSTEM.format(company=company, ticker=ticker)),
        headlines,
        HumanMessage(f"Your research notes (tool results):\n\n{research}"),
        HumanMessage(NEWS_ANALYST_FINAL_INSTRUCTION),
    ]


def verify_citations(analysis: NewsAnalysis, article_count: int) -> tuple[NewsAnalysis, int]:
    """Drop invented article ids, and developments left with no real source."""
    valid = range(1, article_count + 1)
    developments = []
    for dev in analysis.developments:
        ids = [i for i in dict.fromkeys(dev.article_ids) if i in valid]
        if ids:
            developments.append(dev.model_copy(update={"article_ids": ids}))
    dropped = len(analysis.developments) - len(developments)
    cleaned = analysis.model_copy(
        update={
            "developments": developments,
            "irrelevant_article_ids": [i for i in analysis.irrelevant_article_ids if i in valid],
        }
    )
    return cleaned, dropped


async def news_analyst(
    state: BriefingState, ctx: StepContext, deps: AgentDependencies
) -> dict[str, Any]:
    if state.news is None or not state.news.articles:
        raise ValueError("news analyst requires articles")

    workspace = NewsWorkspace(state.news.articles[: deps.settings.news_articles_for_analysis])
    company = state.company_name or state.ticker
    tools = build_news_tools(
        workspace,
        deps.news,
        ticker=state.ticker,
        company_name=state.company_name,
        limit=deps.settings.news_articles_for_analysis,
    )
    messages = NEWS_ANALYST_PROMPT.format_messages(
        company=company, ticker=state.ticker, headlines=workspace.headlines()
    )
    history = await run_tool_loop(
        deps.llm, messages, tools, max_rounds=deps.settings.news_max_tool_rounds, ctx=ctx
    )

    final_messages = research_summary(history, company=company, ticker=state.ticker)
    result = await deps.llm.structured(final_messages, NewsAnalysis)
    ctx.record_structured(result)
    analysis, dropped = verify_citations(result.value, len(workspace.articles))

    ctx.detail = (
        f"{len(analysis.developments)} developments from {len(workspace.articles)} articles, "
        f"{len(analysis.irrelevant_article_ids)} judged irrelevant"
    )
    update: dict[str, Any] = {
        "news_analysis": analysis,
        # Ids refer to positions in this list, so it must be what the report renders.
        "news": state.news.model_copy(update={"articles": workspace.articles}),
    }
    if dropped:
        update["warnings"] = [
            WorkflowWarning(
                source=Step.NEWS_ANALYST,
                code="unsourced_developments_removed",
                message=f"{dropped} news item(s) without a verifiable source were removed.",
                severity="info",
            )
        ]
    return update
