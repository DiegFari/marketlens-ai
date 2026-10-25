"""Probe the real FMP and NewsAPI with a set of tickers and assess data quality.

Runs every ticker through the production client code (rate limiting, error mapping,
parsing). It saves the raw provider responses next to the parsed domain objects, then
reports on:

* metrics:  which ratios are populated, and which expected fields are absent from the
            raw payload (a field-name mismatch or a plan restriction)
* growth:   whether revenue/earnings growth is reachable on this plan (not yet modelled)
* news:     volume, how much the filters dropped, relevance (headlines that actually
            name the company), source diversity, and text available for sentiment

Cost per ticker: 5 FMP calls and 1 NewsAPI call. The default 6 tickers use ~30 of
FMP's ~250/day and 6 of NewsAPI's ~100/day.

    python scripts/probe_providers.py                    # MSFT NVDA ASML AAPL TSLA SAP
    python scripts/probe_providers.py ASML SAP --days 14

Output goes to samples/<timestamp>/ (git-ignored: provider data is licensed content).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from marketlens.core.config import Settings
from marketlens.domain import MarketSnapshot, NewsFeed
from marketlens.services.base import RetryPolicy
from marketlens.services.errors import DataProviderError
from marketlens.services.fmp import FMPClient
from marketlens.services.fmp.schemas import FMPKeyMetricsTTM, FMPRatiosTTM
from marketlens.services.newsapi import NewsAPIClient
from marketlens.services.rate_limit import AsyncTokenBucket

DEFAULT_TICKERS = ["MSFT", "NVDA", "ASML", "AAPL", "TSLA", "SAP"]
GROWTH_FIELDS = ("revenueGrowth", "netIncomeGrowth", "epsgrowth", "freeCashFlowGrowth")
# Below these, sentiment analysis has too little to work with.
MIN_ARTICLES, MIN_RELEVANCE = 5, 0.6


@dataclass
class Record:
    path: str
    params: dict[str, str]
    status: int
    body: bytes

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError:
            return None


class RecordingTransport(httpx.AsyncBaseTransport):
    """Real HTTP transport that keeps a copy of every response (API keys stripped)."""

    def __init__(self) -> None:
        self._inner = httpx.AsyncHTTPTransport()
        self.records: list[Record] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        body = await response.aread()
        params = {k: v for k, v in request.url.params.items() if k.lower() != "apikey"}
        self.records.append(Record(request.url.path, params, response.status_code, body))
        # Body is already decoded, so drop headers that describe the wire encoding.
        headers = [
            (k, v)
            for k, v in response.headers.items()
            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
        ]
        return httpx.Response(response.status_code, headers=headers, content=body, request=request)

    async def aclose(self) -> None:
        await self._inner.aclose()


@dataclass
class TickerResult:
    ticker: str
    snapshot: MarketSnapshot | None = None
    snapshot_error: str | None = None
    missing_metric_fields: list[str] = field(default_factory=list)
    growth: dict[str, Any] | None = None
    growth_error: str | None = None
    feed: NewsFeed | None = None
    news_error: str | None = None
    raw_articles: int = 0
    title_relevance: float = 0.0
    text_relevance: float = 0.0
    off_topic: list[str] = field(default_factory=list)


def _aliases(model: type[FMPRatiosTTM] | type[FMPKeyMetricsTTM]) -> list[str]:
    return [f.alias for f in model.model_fields.values() if f.alias]


def _first(payload: Any) -> dict[str, Any]:
    return payload[0] if isinstance(payload, list) and payload else {}


def _latest(records: list[Record], path_suffix: str, symbol: str | None = None) -> Record | None:
    matches = [
        r
        for r in records
        if r.path.endswith(path_suffix) and (symbol is None or r.params.get("symbol") == symbol)
    ]
    return matches[-1] if matches else None


def _mentions(text: str | None, terms: list[str]) -> bool:
    return bool(text) and any(
        re.search(rf"\b{re.escape(t)}\b", text or "", re.IGNORECASE) for t in terms
    )


async def probe(
    ticker: str,
    fmp: FMPClient,
    news: NewsAPIClient,
    fmp_rec: RecordingTransport,
    news_rec: RecordingTransport,
    days: int,
) -> TickerResult:
    result = TickerResult(ticker)

    try:
        result.snapshot = await fmp.get_snapshot(ticker)
    except DataProviderError as exc:
        result.snapshot_error = f"{type(exc).__name__}: {exc.message}"

    for path, model in (("/ratios-ttm", FMPRatiosTTM), ("/key-metrics-ttm", FMPKeyMetricsTTM)):
        record = _latest(fmp_rec.records, path, ticker)
        raw = _first(record.json()) if record else {}
        if raw:
            result.missing_metric_fields += [a for a in _aliases(model) if a not in raw]

    try:
        # Not modelled yet: probing whether the plan exposes growth data at all.
        payload = await fmp._get_json("/financial-growth", {"symbol": ticker, "limit": 1})
        row = _first(payload)
        result.growth = {k: row.get(k) for k in ("date", *GROWTH_FIELDS)} if row else {}
    except DataProviderError as exc:
        result.growth_error = f"{type(exc).__name__}: {exc.message}"

    name = result.snapshot.profile.name if result.snapshot else None
    try:
        result.feed = await news.search_company_news(ticker, name, days=days, limit=50)
    except DataProviderError as exc:
        result.news_error = f"{type(exc).__name__}: {exc.message}"

    if result.feed:
        record = _latest(news_rec.records, "/everything")
        raw = record.json() if record else None
        result.raw_articles = len(raw.get("articles", [])) if isinstance(raw, dict) else 0
        terms = [t.strip('"') for t in result.feed.query.split(" OR ")]
        articles = result.feed.articles
        if articles:
            in_title = [a for a in articles if _mentions(a.title, terms)]
            in_text = [a for a in articles if _mentions(f"{a.title} {a.description}", terms)]
            result.title_relevance = len(in_title) / len(articles)
            result.text_relevance = len(in_text) / len(articles)
            result.off_topic = [a.title for a in articles if a not in in_text][:5]
    return result


def save(result: TickerResult, out: Path, fmp_rec: RecordingTransport) -> None:
    folder = out / result.ticker
    (folder / "raw").mkdir(parents=True, exist_ok=True)
    if result.snapshot:
        (folder / "snapshot.json").write_text(result.snapshot.model_dump_json(indent=2))
    if result.feed:
        (folder / "news.json").write_text(result.feed.model_dump_json(indent=2))
    for i, record in enumerate(
        r for r in fmp_rec.records if r.params.get("symbol") == result.ticker
    ):
        name = record.path.rsplit("/", 1)[-1]
        payload = record.json()
        text = (
            json.dumps(payload, indent=2)
            if payload is not None
            else record.body.decode(errors="replace")
        )
        (folder / "raw" / f"fmp_{i}_{name}_{record.status}.json").write_text(text)


def save_news_raw(result: TickerResult, out: Path, record: Record | None) -> None:
    if record is not None:
        payload = record.json()
        (out / result.ticker / "raw" / f"newsapi_everything_{record.status}.json").write_text(
            json.dumps(payload, indent=2)
            if payload is not None
            else record.body.decode(errors="replace")
        )


def render(results: list[TickerResult], days: int) -> str:
    lines = [f"# Provider data quality probe — {datetime.now(UTC):%Y-%m-%d %H:%M} UTC", ""]

    lines += [
        "## Market data (FMP)",
        "",
        "| Ticker | Company | Price | Mkt cap ($bn) | Metrics populated | P/E | Net margin | D/E | Revenue growth |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        if not r.snapshot:
            lines.append(f"| {r.ticker} | ❌ {r.snapshot_error} | | | | | | | |")
            continue
        s, m = r.snapshot, r.snapshot.metrics
        values = m.model_dump() if m else {}
        filled = sum(v is not None for v in values.values())
        growth = (r.growth or {}).get("revenueGrowth")
        lines.append(
            f"| {r.ticker} | {s.profile.name} | {s.quote.price:,.2f} | "
            f"{(s.quote.market_cap or 0) / 1e9:,.0f} | {filled}/{len(values) or 14} | "
            f"{_num(values.get('pe_ratio'))} | {_pct(values.get('net_margin'))} | "
            f"{_num(values.get('debt_to_equity'))} | "
            f"{_pct(growth) if r.growth_error is None else '🔒 ' + r.growth_error.split(':')[0]} |"
        )

    lines += [
        "",
        f"## News (NewsAPI, last {days} days)",
        "",
        "| Ticker | Query | Raw | Kept | Named in headline | Named in headline/desc | Sources | With description |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        if not r.feed:
            lines.append(f"| {r.ticker} | ❌ {r.news_error} | | | | | | |")
            continue
        arts = r.feed.articles
        sources = len({a.source for a in arts})
        with_desc = sum(bool(a.description) for a in arts)
        lines.append(
            f"| {r.ticker} | `{r.feed.query}` | {r.raw_articles} | {len(arts)} | "
            f"{r.title_relevance:.0%} | {r.text_relevance:.0%} | {sources} | {with_desc}/{len(arts)} |"
        )

    lines += ["", "## Flags", ""]
    flags = []
    for r in results:
        if r.missing_metric_fields:
            flags.append(
                f"- **{r.ticker}**: expected FMP fields absent from payload: "
                f"`{', '.join(sorted(set(r.missing_metric_fields)))}`"
            )
        if r.snapshot and r.snapshot.warnings:
            flags.append(f"- **{r.ticker}**: snapshot warnings: {r.snapshot.warnings}")
        if r.feed and len(r.feed.articles) < MIN_ARTICLES:
            flags.append(
                f"- **{r.ticker}**: only {len(r.feed.articles)} articles: thin for sentiment"
            )
        if r.feed and r.feed.articles and r.text_relevance < MIN_RELEVANCE:
            flags.append(
                f"- **{r.ticker}**: low relevance ({r.text_relevance:.0%}); off-topic examples:"
            )
            flags += [f"  - {t}" for t in r.off_topic]
    lines += flags or ["- None 🎉"]

    lines += ["", "## Latest headlines", ""]
    for r in results:
        if r.feed:
            lines.append(f"**{r.ticker}**")
            lines += [
                f"- {a.published_at:%m-%d} · {a.source} · {a.title}" for a in r.feed.articles[:6]
            ]
            lines.append("")
    return "\n".join(lines)


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"


async def main() -> int:
    parser = argparse.ArgumentParser(description="Probe real data providers for data quality")
    parser.add_argument("tickers", nargs="*", default=DEFAULT_TICKERS)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--out", type=Path, default=Path("samples"))
    args = parser.parse_args()

    settings = Settings().data_providers
    if settings.fmp_api_key is None or settings.newsapi_api_key is None:
        print("FMP_API_KEY and NEWSAPI_API_KEY must be set (in .env or the environment).")
        print("  FMP:     https://site.financialmodelingprep.com/developer/docs")
        print("  NewsAPI: https://newsapi.org/register")
        return 2

    fmp_rec, news_rec = RecordingTransport(), RecordingTransport()
    retry = RetryPolicy(max_retries=1)
    fmp = FMPClient(
        settings.fmp_api_key.get_secret_value(),
        base_url=settings.fmp_base_url,
        transport=fmp_rec,
        retry=retry,
        rate_limiter=AsyncTokenBucket(60),
    )
    news = NewsAPIClient(
        settings.newsapi_api_key.get_secret_value(),
        base_url=settings.newsapi_base_url,
        transport=news_rec,
        retry=retry,
        rate_limiter=AsyncTokenBucket(30),
    )

    out = args.out / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    results = []
    async with fmp, news:
        for ticker in [t.upper() for t in args.tickers]:
            print(f"probing {ticker}…", file=sys.stderr)
            result = await probe(ticker, fmp, news, fmp_rec, news_rec, args.days)
            save(result, out, fmp_rec)
            save_news_raw(
                result, out, _latest(news_rec.records, "/everything") if result.feed else None
            )
            results.append(result)

    report = render(results, args.days)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report)
    print(report)
    print(f"\nSaved raw + parsed data and report to {out}/", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
