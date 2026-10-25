"""NewsAPI client (``/v2/everything``).

Docs: https://newsapi.org/docs/endpoints/everything

The free Developer plan allows ~100 requests/day, returns articles with a delay and
only for the last month, and is licensed for development only. Production use needs a
paid plan.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from marketlens.domain import NewsArticle, NewsFeed
from marketlens.services.base import BaseProviderClient
from marketlens.services.errors import (
    DataProviderError,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderUnavailableError,
)
from marketlens.services.newsapi.schemas import NewsAPIResponse

_MAX_PAGE_SIZE = 100
# Found by probing real results: package-index and download-site listings match
# company names in their descriptions but carry no market information.
DEFAULT_EXCLUDED_DOMAINS = "pypi.org,rlsbb.cc"

_ERROR_TYPES: dict[str, type[DataProviderError]] = {
    "apiKeyDisabled": ProviderAuthError,
    "apiKeyInvalid": ProviderAuthError,
    "apiKeyMissing": ProviderAuthError,
    "apiKeyExhausted": ProviderRateLimitError,
    "rateLimited": ProviderRateLimitError,
    "parameterInvalid": ProviderRequestError,
    "parametersMissing": ProviderRequestError,
    "sourcesTooMany": ProviderRequestError,
    "sourceDoesNotExist": ProviderRequestError,
    "unexpectedError": ProviderUnavailableError,
}

# Legal-form words that make a company-name search needlessly strict.
_LEGAL_SUFFIXES = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
    "plc", "llc", "lp", "ag", "sa", "se", "nv", "n.v", "holding", "holdings", "group",
}  # fmt: skip
_TOKEN_SPLIT = re.compile(r"[\s,]+")
# Short tickers (F, T, GE) match unrelated words, so search them by name only.
_MIN_TICKER_SEARCH_LENGTH = 3


def _top_up(primary: list[NewsArticle], extra: list[NewsArticle], limit: int) -> list[NewsArticle]:
    """Keep every primary article, fill remaining slots from ``extra``, newest first."""
    seen = {a.url for a in primary} | {a.title.casefold() for a in primary}
    fill = [a for a in extra if a.url not in seen and a.title.casefold() not in seen]
    combined = primary + fill[: max(0, limit - len(primary))]
    return sorted(combined, key=lambda a: a.published_at, reverse=True)


def build_company_query(symbol: str, company_name: str | None) -> str:
    """Build a NewsAPI query that matches the company name or its ticker.

    >>> build_company_query("ASML.AS", "ASML Holding N.V.")
    '"ASML"'
    >>> build_company_query("MSFT", "Microsoft Corporation")
    '"Microsoft" OR "MSFT"'
    """
    base_symbol = re.split(r"[.-]", symbol, maxsplit=1)[0]
    terms: list[str] = []
    if company_name:
        tokens = [t for t in _TOKEN_SPLIT.split(company_name.strip()) if t]
        while len(tokens) > 1 and tokens[-1].lower().rstrip(".") in _LEGAL_SUFFIXES:
            tokens.pop()
        if tokens:
            terms.append(" ".join(tokens))
    if len(base_symbol) >= _MIN_TICKER_SEARCH_LENGTH or not terms:
        terms.append(base_symbol)

    seen: set[str] = set()
    unique: list[str] = []
    for term in (t.replace('"', "") for t in terms):
        if term.casefold() not in seen:
            seen.add(term.casefold())
            unique.append(term)
    return " OR ".join(f'"{t}"' for t in unique)


class NewsAPIClient(BaseProviderClient):
    provider = "newsapi"
    DEFAULT_BASE_URL = "https://newsapi.org/v2"

    SEARCH_TTL_SECONDS = 15 * 60.0

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        exclude_domains: str = DEFAULT_EXCLUDED_DOMAINS,
        min_headline_articles: int = 10,
        **options: Any,
    ) -> None:
        # Header auth keeps the key out of URLs (and therefore out of proxy/access logs).
        super().__init__(base_url=base_url, headers={"X-Api-Key": api_key}, **options)
        self._exclude_domains = exclude_domains
        self._min_headline_articles = min_headline_articles

    async def search_company_news(
        self,
        symbol: str,
        company_name: str | None,
        *,
        days: int = 7,
        limit: int = 20,
        now: datetime | None = None,
    ) -> NewsFeed:
        """Headline matches first; broaden to descriptions only when coverage is thin.

        Measured on real data (6 large caps, 7 days): searching titles and descriptions
        gave 54% of articles that named the company in the headline (package releases,
        passing mentions, "Apple" the fruit). Searching titles only gave 100%, but too
        few articles for less-covered names such as ASML. Hence the two-step search.
        """
        if not 1 <= limit <= _MAX_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {_MAX_PAGE_SIZE}")
        if days < 1:
            raise ValueError("days must be positive")

        query = build_company_query(symbol, company_name)
        since = (now or datetime.now(UTC)) - timedelta(days=days)
        params = {
            "q": query,
            "searchIn": "title",
            # Day granularity keeps the cache key stable for the whole day.
            "from": since.date().isoformat(),
            "language": "en",
            "sortBy": "publishedAt",
            # Over-fetch so de-duplication and filtering still leave `limit` articles.
            "pageSize": min(_MAX_PAGE_SIZE, limit * 2),
        }
        if self._exclude_domains:
            params["excludeDomains"] = self._exclude_domains

        headline = await self._search(params)
        articles = headline.to_articles(limit)
        total_results = headline.total_results
        broadened = len(articles) < min(self._min_headline_articles, limit)

        if broadened:
            broad = await self._search({**params, "searchIn": "title,description"})
            total_results = max(total_results, broad.total_results)
            articles = _top_up(articles, broad.to_articles(limit), limit)

        return NewsFeed(
            symbol=symbol,
            query=query,
            total_results=total_results,
            articles=articles,
            broadened=broadened,
            source=self.provider,
            retrieved_at=datetime.now(UTC),
        )

    async def _search(self, params: dict[str, Any]) -> NewsAPIResponse:
        payload = await self._get_json("/everything", params, cache_ttl=self.SEARCH_TTL_SECONDS)
        return self._validate(NewsAPIResponse, payload)

    def _error_for(self, response: httpx.Response, payload: Any) -> DataProviderError | None:
        if isinstance(payload, dict) and payload.get("status") == "error":
            code = str(payload.get("code", ""))
            message = str(payload.get("message") or code or "unknown error")
            error_type = _ERROR_TYPES.get(code)
            if error_type is ProviderRateLimitError:
                # NewsAPI quotas are daily; it sends no Retry-After, so fail fast.
                return ProviderRateLimitError(
                    self.provider, message, status_code=response.status_code
                )
            if error_type is not None:
                return error_type(self.provider, message, status_code=response.status_code)
        return super()._error_for(response, payload)
