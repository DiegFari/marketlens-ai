"""Read-only company data endpoints.

These expose the provider integrations directly. They are useful for verifying a
deployment, and they are the same calls the agent workflow makes.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Path, Query

from marketlens.api.dependencies import MarketDataDep, NewsDep, ProvidersDep
from marketlens.api.schemas.common import ErrorResponse
from marketlens.domain import MarketSnapshot, NewsFeed, Ticker
from marketlens.services.errors import DataProviderError, TickerNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/companies", tags=["companies"])

TickerPath = Annotated[Ticker, Path(description="Ticker symbol, e.g. MSFT", examples=["MSFT"])]

_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    404: {"model": ErrorResponse, "description": "Unknown ticker"},
    422: {"model": ErrorResponse, "description": "Invalid ticker"},
    502: {"model": ErrorResponse, "description": "Data provider error"},
    503: {
        "model": ErrorResponse,
        "description": "Provider unavailable, rate-limited or not configured",
    },
}


@router.get("/{ticker}/snapshot", response_model=MarketSnapshot, responses=_ERROR_RESPONSES)
async def get_snapshot(ticker: TickerPath, market_data: MarketDataDep) -> MarketSnapshot:
    """Quote, company profile and TTM financial metrics."""
    return await market_data.get_snapshot(ticker)


@router.get("/{ticker}/news", response_model=NewsFeed, responses=_ERROR_RESPONSES)
async def get_news(
    ticker: TickerPath,
    news: NewsDep,
    providers: ProvidersDep,
    days: Annotated[int, Query(ge=1, le=30, description="Look-back window")] = 7,
    limit: Annotated[int, Query(ge=1, le=50, description="Maximum articles")] = 20,
) -> NewsFeed:
    """Recent English-language news about the company, newest first."""
    company_name = None
    if providers.market_data is not None:
        # The company name gives far better news matches than the ticker alone, but
        # news should still work when the market-data provider is having a bad day.
        try:
            company_name = (await providers.market_data.get_profile(ticker)).name
        except TickerNotFoundError:
            raise
        except DataProviderError as exc:
            logger.warning(
                "company name lookup failed; searching by ticker",
                extra={"ticker": ticker, "error": type(exc).__name__},
            )
    return await news.search_company_news(ticker, company_name, days=days, limit=limit)
