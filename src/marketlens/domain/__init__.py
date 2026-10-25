"""Provider-agnostic domain models.

Agents, API routes and reports depend on these types only. Provider clients translate
their raw payloads into them, so swapping a data provider never ripples upwards.
"""

from marketlens.domain.market import (
    CompanyProfile,
    FinancialMetrics,
    MarketSnapshot,
    StockQuote,
)
from marketlens.domain.news import NewsArticle, NewsFeed
from marketlens.domain.ticker import Ticker, normalise_ticker

__all__ = [
    "CompanyProfile",
    "FinancialMetrics",
    "MarketSnapshot",
    "NewsArticle",
    "NewsFeed",
    "StockQuote",
    "Ticker",
    "normalise_ticker",
]
