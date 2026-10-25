"""End-to-end and stress test for a running MarketLens API.

Suites:
  data     real-looking data through the full stack
  faults   upstream failure scenarios (needs scripts/fake_providers.py)
  input    hostile and malformed client input
  load     concurrent load, cache effectiveness and rate-limiter compliance

Invariants checked on every response: no 5xx except the documented 502/503 provider
codes, every error uses the JSON envelope, the envelope's request_id matches the
X-Request-ID header, and provider API keys never appear in a response.

Run: ``python scripts/stress_test.py --api http://127.0.0.1:8000 --upstream http://127.0.0.1:9100``
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

SECRETS = ("fake-fmp-key", "fake-news-key")
ALLOWED_5XX = {
    (502, "upstream_error"),
    (502, "upstream_plan_restricted"),
    (503, "upstream_unavailable"),
    (503, "upstream_rate_limited"),
    (503, "provider_not_configured"),
    (501, "not_implemented"),
}

GREEN, RED, DIM, BOLD, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


@dataclass
class Report:
    passed: int = 0
    failures: list[str] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        mark = f"{GREEN}PASS{RESET}" if ok else f"{RED}FAIL{RESET}"
        print(f"  {mark}  {name:<52} {DIM}{detail}{RESET}")
        if ok:
            self.passed += 1
        else:
            self.failures.append(f"{name}: {detail}")


def invariant_violations(response: httpx.Response) -> list[str]:
    """Contract every response must satisfy, whatever the input."""
    problems = []
    body = response.text
    if any(secret in body for secret in SECRETS):
        problems.append("API key leaked in body")
    if "X-Request-ID" not in response.headers:
        problems.append("missing X-Request-ID")
    if response.status_code >= 400:
        try:
            error = response.json()["error"]
            code = error["code"]
            if error.get("request_id") != response.headers.get("X-Request-ID"):
                problems.append("request_id mismatch")
        except (ValueError, KeyError, TypeError):
            return [*problems, f"no error envelope: {body[:60]!r}"]
        if response.status_code >= 500 and (response.status_code, code) not in ALLOWED_5XX:
            problems.append(f"unexpected {response.status_code} {code}")
    return problems


def describe(response: httpx.Response, elapsed: float) -> str:
    code = ""
    if response.status_code >= 400:
        try:
            code = response.json()["error"]["code"]
        except (ValueError, KeyError, TypeError):
            code = "<no envelope>"
    return f"{response.status_code} {code} ({elapsed * 1000:.0f} ms)".replace("  ", " ")


async def timed(
    client: httpx.AsyncClient, method: str, url: str, **kw: Any
) -> tuple[httpx.Response, float]:
    start = time.perf_counter()
    response = await client.request(method, url, **kw)
    return response, time.perf_counter() - start


# --------------------------------------------------------------------------- suites


async def suite_data(client: httpx.AsyncClient, report: Report) -> None:
    print(f"\n{BOLD}1. Data through the full stack{RESET}")
    rows = []
    for ticker in ("MSFT", "AAPL", "NVDA", "ASML"):
        snap, t1 = await timed(client, "GET", f"/api/v1/companies/{ticker}/snapshot")
        news, t2 = await timed(client, "GET", f"/api/v1/companies/{ticker}/news?limit=5")
        ok = snap.status_code == 200 and news.status_code == 200
        report.check(
            f"{ticker} snapshot + news", ok, f"{describe(snap, t1)} / {describe(news, t2)}"
        )
        if ok:
            s, n = snap.json(), news.json()
            rows.append(
                (
                    ticker,
                    s["profile"]["name"],
                    s["quote"]["price"],
                    s["metrics"]["pe_ratio"],
                    len(n["articles"]),
                    n["query"],
                )
            )

    print(f"\n  {'Ticker':<7}{'Company':<24}{'Price':>9}{'P/E':>7}{'News':>6}  Query")
    for ticker, name, price, pe, articles, query in rows:
        print(f"  {ticker:<7}{name:<24}{price:>9.2f}{pe:>7.1f}{articles:>6}  {query}")


FAULTS = [
    # ticker, endpoint, expected status, expected code (None = success)
    ("ZZZZ", "snapshot", 404, "ticker_not_found"),
    ("FLAKY", "snapshot", 200, None),
    ("DOWN", "snapshot", 503, "upstream_unavailable"),
    ("SLOW", "snapshot", 503, "upstream_unavailable"),
    ("LIMIT", "snapshot", 503, "upstream_rate_limited"),
    ("RETRY", "snapshot", 200, None),
    ("LIMIT200", "snapshot", 503, "upstream_rate_limited"),
    ("GARBAGE", "snapshot", 502, "upstream_error"),
    ("SHAPE", "snapshot", 502, "upstream_error"),
    ("NOPRICE", "snapshot", 502, "upstream_error"),
    ("PREMIUM", "snapshot", 200, None),
    ("ZZZZ", "news", 404, "ticker_not_found"),
    ("DOWN", "news", 503, "upstream_unavailable"),
    ("LIMIT", "news", 503, "upstream_rate_limited"),
]


async def suite_faults(client: httpx.AsyncClient, report: Report) -> None:
    print(f"\n{BOLD}2. Upstream failure scenarios{RESET}")
    for ticker, endpoint, status, code in FAULTS:
        response, elapsed = await timed(client, "GET", f"/api/v1/companies/{ticker}/{endpoint}")
        actual_code = (
            response.json().get("error", {}).get("code") if response.status_code >= 400 else None
        )
        problems = invariant_violations(response)
        ok = response.status_code == status and actual_code == code and not problems
        detail = describe(response, elapsed) + (f" {problems}" if problems else "")
        if ticker == "PREMIUM" and response.status_code == 200:
            detail += f" warnings={response.json()['warnings']}"
        report.check(f"{ticker:<9} {endpoint:<8} → {status} {code or 'OK'}", ok, detail)


BAD_TICKERS = [
    "",
    " ",
    "a" * 10_000,
    "../../etc/passwd",
    "MSFT;DROP TABLE users",
    "<script>alert(1)</script>",
    "MSFT\x00",
    "\u041cSFT",
    "📈",
    "123",
    "-MSFT",
    "MSFT..",
    "${jndi:ldap://x}",
    "%00",
    "MSFT OR 1=1",
]
NORMALISED_TICKERS = [" msft ", "msft", "Msft"]  # should be accepted as MSFT

BAD_QUERIES = [
    "days=-1",
    "days=abc",
    "days=31",
    "days=99999999999999999999",
    "limit=0",
    "limit=1.5",
    "limit=",
    "limit=51",
    "days=7&days=8",
]

BAD_BODIES: list[tuple[str, dict[str, Any]]] = [
    (
        "malformed JSON",
        {"content": b'{"ticker": ', "headers": {"content-type": "application/json"}},
    ),
    ("ticker is a number", {"json": {"ticker": 123}}),
    ("ticker is null", {"json": {"ticker": None}}),
    ("empty object", {"json": {}}),
    ("array body", {"json": ["MSFT"]}),
    ("1 MB ticker", {"json": {"ticker": "A" * 1_000_000}}),
    (
        "text/plain body",
        {"content": b'{"ticker":"MSFT"}', "headers": {"content-type": "text/plain"}},
    ),
    ("injection in ticker", {"json": {"ticker": "'; DROP TABLE reports; --"}}),
]


async def suite_input(client: httpx.AsyncClient, report: Report) -> None:
    print(f"\n{BOLD}3. Hostile and malformed input{RESET}")

    statuses: Counter[int] = Counter()
    problems_seen = []
    for ticker in BAD_TICKERS:
        for endpoint in ("snapshot", "news"):
            response = await client.get(f"/api/v1/companies/{quote(ticker, safe='')}/{endpoint}")
            statuses[response.status_code] += 1
            if response.status_code < 400 or response.status_code >= 500:
                problems_seen.append(f"{ticker[:20]!r}/{endpoint} → {response.status_code}")
            problems_seen += [f"{ticker[:20]!r}: {p}" for p in invariant_violations(response)]
    report.check(
        f"{len(BAD_TICKERS) * 2} invalid tickers rejected with 4xx",
        not problems_seen,
        f"{dict(statuses)} {problems_seen[:3]}",
    )

    for ticker in NORMALISED_TICKERS:
        response = await client.get(f"/api/v1/companies/{quote(ticker, safe='')}/snapshot")
        symbol = response.json().get("symbol") if response.status_code == 200 else None
        report.check(f"{ticker!r} normalised to MSFT", symbol == "MSFT", str(response.status_code))

    for query in BAD_QUERIES:
        response = await client.get(f"/api/v1/companies/MSFT/news?{query}")
        problems = invariant_violations(response)
        expected_ok = query == "days=7&days=8"  # repeated param: last value wins
        ok = (response.status_code == 200) if expected_ok else (response.status_code == 422)
        report.check(
            f"news?{query}", ok and not problems, f"{response.status_code} {problems or ''}"
        )

    for name, kwargs in BAD_BODIES:
        response = await client.post("/api/v1/analysis", **kwargs)
        problems = invariant_violations(response)
        report.check(
            f"POST /analysis: {name}",
            response.status_code == 422 and not problems,
            f"{response.status_code} {problems or ''}",
        )

    for name, method, path in [
        ("unknown route", "GET", "/api/v1/nope"),
        ("wrong method", "GET", "/api/v1/analysis"),
        ("wrong method", "DELETE", "/api/v1/companies/MSFT/snapshot"),
    ]:
        response = await client.request(method, path)
        problems = invariant_violations(response)
        report.check(
            f"{name}: {method} {path}",
            response.status_code in (404, 405) and not problems,
            f"{response.status_code} {problems or ''}",
        )

    for name, value in [
        ("10 KB request id", "x" * 10_000),
        ("request id with markup", "<b>id</b>"),
    ]:
        response = await client.get("/health/live", headers={"X-Request-ID": value})
        echoed = response.headers.get("X-Request-ID", "")
        report.check(
            f"header abuse: {name}",
            echoed != value and len(echoed) == 32,
            f"echoed {len(echoed)} chars",
        )


async def suite_load(
    client: httpx.AsyncClient,
    upstream: httpx.AsyncClient | None,
    report: Report,
    total: int,
    concurrency: int,
    fmp_rpm: float,
) -> None:
    print(f"\n{BOLD}4. Load: {total} requests, {concurrency} concurrent{RESET}")
    if upstream:
        await upstream.post("/_reset")

    paths = [
        f"/api/v1/companies/{t}/{e}"
        for t in ("MSFT", "AAPL", "NVDA", "ASML")
        for e in ("snapshot", "news")
    ]
    semaphore = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    statuses: Counter[int] = Counter()
    violations: list[str] = []

    async def one(i: int) -> None:
        async with semaphore:
            response, elapsed = await timed(client, "GET", paths[i % len(paths)])
            latencies.append(elapsed)
            statuses[response.status_code] += 1
            violations.extend(invariant_violations(response))

    start = time.perf_counter()
    await asyncio.gather(*(one(i) for i in range(total)))
    wall = time.perf_counter() - start

    q = statistics.quantiles(latencies, n=100)
    report.check("all requests succeeded", statuses == Counter({200: total}), str(dict(statuses)))
    report.check("no contract violations under load", not violations, str(violations[:3]))
    print(
        f"  {DIM}throughput {total / wall:.0f} req/s · p50 {q[49] * 1000:.0f} ms · "
        f"p95 {q[94] * 1000:.0f} ms · p99 {q[98] * 1000:.0f} ms{RESET}"
    )

    if not upstream:
        return
    calls = (await upstream.get("/_stats")).json()
    report.check(
        f"cache absorbed load: {total} requests → {calls['total']} upstream calls",
        calls["total"] <= 40,
        str(calls["calls"]),
    )

    # Overload: more distinct (uncacheable) tickers than the FMP budget can serve in the
    # API's 10 s queue limit. Expected: most succeed, the excess is shed with a clean
    # 503 upstream_rate_limited, and nobody waits much beyond the queue limit.
    await upstream.post("/_reset")
    tickers = [f"T{n}" for n in range(1, 31)]
    timings = await asyncio.gather(
        *(timed(client, "GET", f"/api/v1/companies/{t}/snapshot") for t in tickers)
    )
    calls = (await upstream.get("/_stats")).json()
    ceiling = fmp_rpm / 60 + 5  # sustained rate + burst
    outcomes = Counter(
        "ok" if r.status_code == 200 else r.json()["error"]["code"] for r, _ in timings
    )
    slowest = max(elapsed for _, elapsed in timings)
    report.check(
        f"30 cold snapshots ({calls['total']} FMP calls): served or shed",
        set(outcomes) <= {"ok", "upstream_rate_limited"} and outcomes["ok"] > 0,
        str(dict(outcomes)),
    )
    report.check(
        "overload latency bounded by queue limit (≤ 12 s)",
        slowest <= 12,
        f"slowest {slowest:.1f} s",
    )
    report.check(
        f"rate limiter held FMP ≤ {ceiling:.0f} calls/s",
        calls["fmp_peak_per_second"] <= ceiling,
        f"peak {calls['fmp_peak_per_second']}/s",
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="MarketLens end-to-end stress test")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--upstream", help="fake_providers.py URL (enables fault + upstream checks)"
    )
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument(
        "--fmp-rpm", type=float, default=600, help="FMP_REQUESTS_PER_MINUTE of the API"
    )
    args = parser.parse_args()

    report = Report()
    limits = httpx.Limits(max_connections=args.concurrency + 10)
    async with httpx.AsyncClient(base_url=args.api, timeout=60, limits=limits) as client:
        upstream = httpx.AsyncClient(base_url=args.upstream, timeout=10) if args.upstream else None
        if upstream:
            await upstream.post("/_reset")
        await suite_data(client, report)
        if upstream:
            await suite_faults(client, report)
        await suite_input(client, report)
        await suite_load(client, upstream, report, args.requests, args.concurrency, args.fmp_rpm)
        if upstream:
            await upstream.aclose()

    colour = GREEN if not report.failures else RED
    print(f"\n{colour}{BOLD}{report.passed} passed, {len(report.failures)} failed{RESET}")
    for failure in report.failures:
        print(f"  {RED}✗{RESET} {failure}")
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
