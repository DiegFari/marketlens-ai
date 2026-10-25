"""Interfaces the rest of the application depends on.

Routes and the agent workflow accept these protocols rather than concrete
clients, so tests can pass simple fakes and providers can be swapped.
"""

from __future__ import annotations

from typing import Protocol

from marketlens.domain import CompanyProfile, FinancialMetrics, MarketSnapshot, NewsFeed, StockQuote


class MarketDataProvider(Protocol):
    async def get_quote(self, symbol: str) -> StockQuote: ...

    async def get_profile(self, symbol: str) -> CompanyProfile: ...

    async def get_financial_metrics(self, symbol: str) -> FinancialMetrics: ...

    async def get_snapshot(self, symbol: str) -> MarketSnapshot: ...

    async def aclose(self) -> None: ...


class NewsProvider(Protocol):
    async def search_company_news(
        self, symbol: str, company_name: str | None, *, days: int = 7, limit: int = 20
    ) -> NewsFeed: ...

    async def aclose(self) -> None: ...
