"""Raw Financial Modeling Prep ``/stable`` payloads and their mapping to domain models.

Fields are optional and unknown fields are ignored: FMP adds and occasionally renames
fields, and a missing ratio should degrade one number in a report, not fail the request.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from marketlens.domain import CompanyProfile, FinancialMetrics, StockQuote


class _FMPModel(BaseModel):
    model_config = ConfigDict(extra="ignore", alias_generator=to_camel, populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _blank_strings_to_none(cls, data: Any) -> Any:
        # FMP sometimes sends "" for missing numbers and dates.
        if isinstance(data, dict):
            return {key: (None if value == "" else value) for key, value in data.items()}
        return data


class FMPQuote(_FMPModel):
    symbol: str
    name: str | None = None
    price: float
    change: float | None = None
    change_percentage: float | None = None
    open: float | None = None
    previous_close: float | None = None
    day_low: float | None = None
    day_high: float | None = None
    year_low: float | None = None
    year_high: float | None = None
    volume: int | None = None
    market_cap: float | None = None
    price_avg50: float | None = None
    price_avg200: float | None = None
    exchange: str | None = None
    timestamp: datetime | None = None  # unix seconds

    def to_domain(self) -> StockQuote:
        return StockQuote(
            symbol=self.symbol,
            name=self.name,
            price=self.price,
            change=self.change,
            change_percent=self.change_percentage,
            open=self.open,
            previous_close=self.previous_close,
            day_low=self.day_low,
            day_high=self.day_high,
            year_low=self.year_low,
            year_high=self.year_high,
            volume=self.volume,
            market_cap=self.market_cap,
            avg_price_50d=self.price_avg50,
            avg_price_200d=self.price_avg200,
            exchange=self.exchange,
            as_of=self.timestamp,
        )


class FMPProfile(_FMPModel):
    symbol: str
    company_name: str
    description: str | None = None
    sector: str | None = None
    industry: str | None = None
    country: str | None = None
    exchange: str | None = None
    currency: str | None = None
    ceo: str | None = None
    website: str | None = None
    full_time_employees: int | None = None  # sent as a string, e.g. "164000"
    ipo_date: date | None = None
    beta: float | None = None
    is_etf: bool = False
    is_actively_trading: bool = True

    def to_domain(self) -> CompanyProfile:
        return CompanyProfile(
            symbol=self.symbol,
            name=self.company_name,
            description=self.description,
            sector=self.sector,
            industry=self.industry,
            country=self.country,
            exchange=self.exchange,
            currency=self.currency,
            ceo=self.ceo,
            website=self.website,
            employees=self.full_time_employees,
            ipo_date=self.ipo_date,
            beta=self.beta,
            is_etf=self.is_etf,
            is_actively_trading=self.is_actively_trading,
        )


# TTM field names end in "TTM", which the camel-case generator cannot produce.
class FMPRatiosTTM(_FMPModel):
    pe_ratio: float | None = Field(default=None, alias="priceToEarningsRatioTTM")
    price_to_book: float | None = Field(default=None, alias="priceToBookRatioTTM")
    price_to_sales: float | None = Field(default=None, alias="priceToSalesRatioTTM")
    debt_to_equity: float | None = Field(default=None, alias="debtToEquityRatioTTM")
    current_ratio: float | None = Field(default=None, alias="currentRatioTTM")
    gross_margin: float | None = Field(default=None, alias="grossProfitMarginTTM")
    operating_margin: float | None = Field(default=None, alias="operatingProfitMarginTTM")
    net_margin: float | None = Field(default=None, alias="netProfitMarginTTM")
    dividend_yield: float | None = Field(default=None, alias="dividendYieldTTM")


class FMPKeyMetricsTTM(_FMPModel):
    ev_to_ebitda: float | None = Field(default=None, alias="evToEBITDATTM")
    return_on_equity: float | None = Field(default=None, alias="returnOnEquityTTM")
    return_on_assets: float | None = Field(default=None, alias="returnOnAssetsTTM")
    return_on_invested_capital: float | None = Field(
        default=None, alias="returnOnInvestedCapitalTTM"
    )
    free_cash_flow_yield: float | None = Field(default=None, alias="freeCashFlowYieldTTM")


class FMPFinancialGrowth(_FMPModel):
    """Latest annual row of ``/financial-growth`` (verified against a live response)."""

    revenue_growth: float | None = None
    fiscal_year: str | None = None
    period: str | None = None

    @property
    def period_label(self) -> str | None:
        if self.fiscal_year is None:
            return None
        return f"{self.period or 'FY'}{self.fiscal_year}"


def build_financial_metrics(
    ratios: FMPRatiosTTM | None,
    key_metrics: FMPKeyMetricsTTM | None,
    growth: FMPFinancialGrowth | None = None,
) -> FinancialMetrics:
    fields: dict[str, Any] = {}
    for part in (ratios, key_metrics):
        if part is not None:
            fields.update(part.model_dump(exclude_none=True))
    if growth is not None and growth.revenue_growth is not None:
        fields["revenue_growth"] = growth.revenue_growth
        fields["revenue_growth_period"] = growth.period_label
    return FinancialMetrics(**fields)
