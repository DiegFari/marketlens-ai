"""Financial Modeling Prep client (``/stable`` API).

Docs: https://site.financialmodelingprep.com/developer/docs/stable
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from marketlens.domain import CompanyProfile, FinancialMetrics, MarketSnapshot, StockQuote
from marketlens.services.base import BaseProviderClient
from marketlens.services.errors import (
    DataProviderError,
    ProviderAuthError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResponseError,
    TickerNotFoundError,
)
from marketlens.services.fmp.schemas import (
    FMPFinancialGrowth,
    FMPKeyMetricsTTM,
    FMPProfile,
    FMPQuote,
    FMPRatiosTTM,
    build_financial_metrics,
)

logger = logging.getLogger(__name__)


class FMPClient(BaseProviderClient):
    provider = "financial_modeling_prep"
    DEFAULT_BASE_URL = "https://financialmodelingprep.com/stable"

    QUOTE_TTL_SECONDS = 60.0
    PROFILE_TTL_SECONDS = 24 * 3600.0
    METRICS_TTL_SECONDS = 6 * 3600.0

    def __init__(self, api_key: str, *, base_url: str = DEFAULT_BASE_URL, **options: Any) -> None:
        # Header auth (verified against the live API) keeps the key out of URLs, so it
        # cannot leak through httpx's request logging, access logs or proxies.
        super().__init__(base_url=base_url, headers={"apikey": api_key}, **options)

    async def get_quote(self, symbol: str) -> StockQuote:
        payload = await self._get_json(
            "/quote", {"symbol": symbol}, cache_ttl=self.QUOTE_TTL_SECONDS
        )
        return self._validate(FMPQuote, self._first(payload, symbol)).to_domain()

    async def get_profile(self, symbol: str) -> CompanyProfile:
        payload = await self._get_json(
            "/profile", {"symbol": symbol}, cache_ttl=self.PROFILE_TTL_SECONDS
        )
        return self._validate(FMPProfile, self._first(payload, symbol)).to_domain()

    async def get_financial_metrics(self, symbol: str) -> FinancialMetrics:
        ttl = self.METRICS_TTL_SECONDS
        results: tuple[Any, ...] = await asyncio.gather(
            self._get_json("/ratios-ttm", {"symbol": symbol}, cache_ttl=ttl),
            self._get_json("/key-metrics-ttm", {"symbol": symbol}, cache_ttl=ttl),
            self._get_json("/financial-growth", {"symbol": symbol, "limit": 1}, cache_ttl=ttl),
            return_exceptions=True,
        )
        ratios_payload, metrics_payload, growth_payload = results
        if isinstance(ratios_payload, BaseException):
            raise ratios_payload
        if isinstance(metrics_payload, BaseException):
            raise metrics_payload

        # Growth is a nice-to-have: losing it must not cost the 14 TTM metrics.
        growth: FMPFinancialGrowth | None = None
        if isinstance(growth_payload, DataProviderError):
            logger.warning(
                "revenue growth unavailable",
                extra={"symbol": symbol, "error": type(growth_payload).__name__},
            )
        elif isinstance(growth_payload, BaseException):
            raise growth_payload
        elif (row := self._first_or_none(growth_payload)) is not None:
            growth = self._validate(FMPFinancialGrowth, row)

        # ETFs and recent listings legitimately have no TTM data: empty, not an error.
        ratios = self._first_or_none(ratios_payload)
        metrics = self._first_or_none(metrics_payload)
        return build_financial_metrics(
            self._validate(FMPRatiosTTM, ratios) if ratios is not None else None,
            self._validate(FMPKeyMetricsTTM, metrics) if metrics is not None else None,
            growth,
        )

    async def get_snapshot(self, symbol: str) -> MarketSnapshot:
        """Quote and profile are required; metrics are best-effort (often plan-gated)."""
        quote, profile, metrics_result = await asyncio.gather(
            self.get_quote(symbol),
            self.get_profile(symbol),
            self.get_financial_metrics(symbol),
            return_exceptions=True,
        )
        if isinstance(quote, BaseException):
            raise quote
        if isinstance(profile, BaseException):
            raise profile

        metrics: FinancialMetrics | None = None
        warnings: list[str] = []
        if isinstance(metrics_result, DataProviderError):
            logger.warning(
                "financial metrics unavailable",
                extra={"symbol": symbol, "error": type(metrics_result).__name__},
            )
            warnings.append(f"Financial metrics unavailable: {metrics_result.message}")
        elif isinstance(metrics_result, BaseException):
            raise metrics_result
        else:
            metrics = metrics_result

        return MarketSnapshot(
            symbol=quote.symbol,
            quote=quote,
            profile=profile,
            metrics=metrics,
            source=self.provider,
            retrieved_at=datetime.now(UTC),
            warnings=warnings,
        )

    # ------------------------------------------------------------------ helpers

    def _first(self, payload: Any, symbol: str) -> Any:
        if not isinstance(payload, list):
            raise ProviderResponseError(self.provider, "expected a JSON array")
        if not payload:
            raise TickerNotFoundError(self.provider, symbol)
        return payload[0]

    def _first_or_none(self, payload: Any) -> Any:
        if not isinstance(payload, list):
            raise ProviderResponseError(self.provider, "expected a JSON array")
        return payload[0] if payload else None

    def _error_message(self, payload: Any) -> str | None:
        if isinstance(payload, dict) and payload.get("Error Message"):
            return str(payload["Error Message"])
        return super()._error_message(payload)

    def _error_for(self, response: httpx.Response, payload: Any) -> DataProviderError | None:
        # FMP has historically returned some errors as HTTP 200 with an "Error Message"
        # body, so classify by text when the status code says nothing.
        message = self._error_message(payload)
        if response.is_success and message:
            lowered = message.lower()
            status = response.status_code
            if "limit reach" in lowered:
                return ProviderRateLimitError(self.provider, message, status_code=status)
            if "api key" in lowered:
                return ProviderAuthError(self.provider, message, status_code=status)
            if any(word in lowered for word in ("premium", "restricted", "subscription")):
                return ProviderPermissionError(self.provider, message, status_code=status)
            return ProviderRequestError(self.provider, message, status_code=status)
        return super()._error_for(response, payload)
