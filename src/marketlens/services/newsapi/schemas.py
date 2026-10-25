"""Raw NewsAPI ``/v2/everything`` payload and its mapping to domain models."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from marketlens.domain import NewsArticle

# NewsAPI keeps placeholders for articles withdrawn by publishers.
_REMOVED_MARKER = "[Removed]"
# Truncated content ends with e.g. "… [+2345 chars]".
_TRUNCATION_SUFFIX = re.compile(r"\s*(…|\.\.\.)?\s*\[\+\d+ chars\]\s*$")


class _NewsAPIModel(BaseModel):
    model_config = ConfigDict(extra="ignore", alias_generator=to_camel, populate_by_name=True)


class NewsAPISource(_NewsAPIModel):
    id: str | None = None
    name: str | None = None


class NewsAPIArticle(_NewsAPIModel):
    source: NewsAPISource | None = None
    author: str | None = None
    title: str | None = None
    description: str | None = None
    url: str | None = None
    published_at: datetime
    content: str | None = None

    def to_domain(self) -> NewsArticle | None:
        """The domain article, or None for removed or incomplete entries."""
        if not self.title or not self.url or self.title == _REMOVED_MARKER:
            return None
        return NewsArticle(
            title=self.title.strip(),
            url=self.url,
            source=self.source.name if self.source else None,
            author=self.author,
            description=self.description,
            published_at=self.published_at,
            content_snippet=_TRUNCATION_SUFFIX.sub("", self.content) if self.content else None,
        )


class NewsAPIResponse(_NewsAPIModel):
    status: str
    total_results: int = 0
    articles: list[NewsAPIArticle] = Field(default_factory=list)

    def to_articles(self, limit: int) -> list[NewsArticle]:
        """Usable articles, newest first, de-duplicated by URL and by headline."""
        usable = [a for a in (raw.to_domain() for raw in self.articles) if a is not None]
        newest_first = sorted(usable, key=lambda a: a.published_at, reverse=True)

        # Same URL twice: the headline was edited, so keep the newest version.
        by_url = _unique(newest_first, key=lambda a: a.url)
        # Same headline under different URLs: syndication, so keep the original (earliest).
        by_title = _unique(reversed(by_url), key=lambda a: a.title.casefold())

        return sorted(by_title, key=lambda a: a.published_at, reverse=True)[:limit]


def _unique(
    articles: Iterable[NewsArticle], key: Callable[[NewsArticle], str]
) -> list[NewsArticle]:
    seen: set[str] = set()
    unique: list[NewsArticle] = []
    for article in articles:
        if key(article) not in seen:
            seen.add(key(article))
            unique.append(article)
    return unique
