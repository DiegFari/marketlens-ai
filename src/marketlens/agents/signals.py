"""Deterministic indicators derived from market data.

Arithmetic is done here, not by the model: LLMs are unreliable calculators, and every
number in a briefing must be traceable to provider data.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from marketlens.domain import MarketSnapshot, StockQuote

Trend = Literal["uptrend", "downtrend", "mixed"]


class PriceSignals(BaseModel):
    model_config = ConfigDict(frozen=True)

    day_change_percent: float | None = None
    position_in_52w_range: float | None = None  # 0 = at the low, 1 = at the high
    percent_below_52w_high: float | None = None
    percent_vs_50d_avg: float | None = None
    percent_vs_200d_avg: float | None = None
    trend: Trend | None = None


def _pct(value: float | None, reference: float | None) -> float | None:
    if value is None or not reference:
        return None
    return round((value - reference) / reference * 100, 2)


def derive_price_signals(quote: StockQuote) -> PriceSignals:
    low, high, price = quote.year_low, quote.year_high, quote.price
    position = None
    if low is not None and high is not None and high > low:
        position = round(min(1.0, max(0.0, (price - low) / (high - low))), 3)

    below_high = round((high - price) / high * 100, 2) if high else None
    trend: Trend | None = None
    ma50, ma200 = quote.avg_price_50d, quote.avg_price_200d
    if ma50 is not None and ma200 is not None:
        if price > ma50 > ma200:
            trend = "uptrend"
        elif price < ma50 < ma200:
            trend = "downtrend"
        else:
            trend = "mixed"

    return PriceSignals(
        day_change_percent=quote.change_percent,
        position_in_52w_range=position,
        percent_below_52w_high=below_high,
        percent_vs_50d_avg=_pct(price, ma50),
        percent_vs_200d_avg=_pct(price, ma200),
        trend=trend,
    )


def market_facts(snapshot: MarketSnapshot) -> dict[str, object]:
    """Compact, model-friendly view of a snapshot (no long text, no nulls)."""
    profile = snapshot.profile
    facts: dict[str, object] = {
        "company": profile.name,
        "sector": profile.sector,
        "industry": profile.industry,
        "country": profile.country,
        "currency": profile.currency,
        "beta": profile.beta,
        "quote": snapshot.quote.model_dump(exclude={"symbol", "name", "as_of"}, exclude_none=True),
        "price_signals": derive_price_signals(snapshot.quote).model_dump(exclude_none=True),
        "ttm_metrics": snapshot.metrics.model_dump(exclude_none=True) if snapshot.metrics else None,
        "data_as_of": snapshot.quote.as_of.isoformat() if snapshot.quote.as_of else None,
    }
    return {k: v for k, v in facts.items() if v not in (None, {}, [])}
