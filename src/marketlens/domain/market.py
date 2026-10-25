from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from marketlens.domain.base import DomainModel


class StockQuote(DomainModel):
    symbol: str
    name: str | None = None
    price: float
    change: float | None = None
    change_percent: float | None = Field(default=None, description="Percent, e.g. 1.25 = +1.25%")
    open: float | None = None
    previous_close: float | None = None
    day_low: float | None = None
    day_high: float | None = None
    year_low: float | None = None
    year_high: float | None = None
    volume: int | None = None
    market_cap: float | None = None
    avg_price_50d: float | None = None
    avg_price_200d: float | None = None
    exchange: str | None = None
    as_of: datetime | None = None


class CompanyProfile(DomainModel):
    symbol: str
    name: str
    description: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    exchange: str | None = None
    currency: str | None = None
    ceo: str | None = None
    website: str | None = None
    employees: int | None = None
    ipo_date: date | None = None
    beta: float | None = None
    is_etf: bool = False
    is_actively_trading: bool = True


class FinancialMetrics(DomainModel):
    """Trailing-twelve-month valuation, profitability and balance-sheet ratios.

    Ratios are fractions (0.25 = 25%), as reported by the provider.
    """

    pe_ratio: float | None = None
    price_to_book: float | None = None
    price_to_sales: float | None = None
    ev_to_ebitda: float | None = None
    debt_to_equity: float | None = None
    current_ratio: float | None = None
    return_on_equity: float | None = None
    return_on_assets: float | None = None
    return_on_invested_capital: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    net_margin: float | None = None
    dividend_yield: float | None = None
    free_cash_flow_yield: float | None = None
    # Not TTM: year-over-year growth of the latest reported fiscal year.
    revenue_growth: float | None = Field(
        default=None, description="Revenue growth of the latest fiscal year (0.18 = +18%)"
    )
    revenue_growth_period: str | None = Field(
        default=None, description="Fiscal period the growth refers to, e.g. FY2026"
    )


class MarketSnapshot(DomainModel):
    """Everything the Market Data agent needs about one company, in one object."""

    symbol: str
    quote: StockQuote
    profile: CompanyProfile
    metrics: FinancialMetrics | None = None
    source: str = Field(description="Data provider, for attribution in reports")
    retrieved_at: datetime
    # Partial-data notes (e.g. metrics not in the plan) so reports can disclose gaps.
    warnings: list[str] = Field(default_factory=list)
