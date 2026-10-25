"""Builds provider clients from settings and owns their lifecycle.

One instance lives on ``app.state`` for the lifetime of the process, so HTTP
connection pools, rate limiters and caches are shared by all requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from marketlens.core.config import DataProviderSettings
from marketlens.services.base import RetryPolicy
from marketlens.services.cache import TTLCache
from marketlens.services.fmp import FMPClient
from marketlens.services.newsapi import NewsAPIClient
from marketlens.services.protocols import MarketDataProvider, NewsProvider
from marketlens.services.rate_limit import AsyncTokenBucket


@dataclass
class DataProviders:
    market_data: MarketDataProvider | None = None
    news: NewsProvider | None = None

    @classmethod
    def from_settings(cls, settings: DataProviderSettings) -> DataProviders:
        retry = RetryPolicy(max_retries=settings.data_provider_max_retries)

        def options(requests_per_minute: float) -> dict[str, Any]:
            return {
                "timeout_seconds": settings.data_provider_timeout_seconds,
                "retry": retry,
                "rate_limiter": AsyncTokenBucket(requests_per_minute),
                "rate_limit_max_wait_seconds": settings.data_provider_rate_limit_max_wait_seconds,
                "cache": TTLCache() if settings.data_provider_cache_enabled else None,
            }

        market_data = None
        if settings.fmp_api_key is not None:
            market_data = FMPClient(
                settings.fmp_api_key.get_secret_value(),
                base_url=settings.fmp_base_url,
                **options(settings.fmp_requests_per_minute),
            )

        news = None
        if settings.newsapi_api_key is not None:
            news = NewsAPIClient(
                settings.newsapi_api_key.get_secret_value(),
                base_url=settings.newsapi_base_url,
                exclude_domains=settings.newsapi_exclude_domains,
                min_headline_articles=settings.newsapi_min_headline_articles,
                **options(settings.newsapi_requests_per_minute),
            )

        return cls(market_data=market_data, news=news)

    async def aclose(self) -> None:
        if self.market_data is not None:
            await self.market_data.aclose()
        if self.news is not None:
            await self.news.aclose()
