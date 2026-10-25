from __future__ import annotations

from datetime import datetime

from pydantic import Field

from marketlens.domain.base import DomainModel


class NewsArticle(DomainModel):
    title: str
    url: str
    source: str | None = None
    author: str | None = None
    description: str | None = None
    published_at: datetime
    content_snippet: str | None = None


class NewsFeed(DomainModel):
    symbol: str
    query: str = Field(description="The search query sent to the provider")
    total_results: int = Field(description="Matches reported by the provider before filtering")
    articles: list[NewsArticle]
    broadened: bool = Field(
        default=False,
        description="Headline matches were too few, so description matches were added",
    )
    source: str
    retrieved_at: datetime
