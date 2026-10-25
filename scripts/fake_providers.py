"""Fake Financial Modeling Prep + NewsAPI server for end-to-end and chaos testing.

Serves realistic payloads for a few real tickers and injects failures for scenario
tickers, so the whole stack (HTTP, retries, rate limiting, caching, error mapping) can
be exercised without API keys or quota. Point the API at it with::

    FMP_API_KEY=fake-fmp-key FMP_BASE_URL=http://127.0.0.1:9100/stable
    NEWSAPI_API_KEY=fake-news-key NEWSAPI_BASE_URL=http://127.0.0.1:9100/v2

Scenario tickers (FMP endpoints):

    ZZZZ      empty result            -> ticker not found
    FLAKY     503 twice, then OK      -> recovered by retries
    DOWN      always 503              -> retries exhausted
    SLOW      responds after 3 s      -> client timeout
    LIMIT     429, no Retry-After     -> fail fast (quota exhausted)
    RETRY     429 Retry-After: 1 once -> waits, then OK
    LIMIT200  200 + "Limit Reach"     -> FMP's error-in-a-200 quirk
    GARBAGE   200 text/html           -> not JSON
    SHAPE     200 {"unexpected": ...} -> wrong JSON shape
    NOPRICE   quote without price     -> fails validation
    PREMIUM   metrics endpoints 402   -> snapshot degrades with a warning
    T<n>      synthetic company n     -> for rate-limiter tests

Run: ``python scripts/fake_providers.py [--port 9100]``
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

FMP_KEY = "fake-fmp-key"
NEWS_KEY = "fake-news-key"
FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"

COMPANIES = {
    "MSFT": ("Microsoft Corporation", 512.34),
    "AAPL": ("Apple Inc.", 262.82),
    "NVDA": ("NVIDIA Corporation", 181.16),
    "ASML": ("ASML Holding N.V.", 1012.50),
    # Scenario tickers that eventually succeed need a company behind them.
    "FLAKY": ("Flaky Networks Inc.", 42.0),
    "RETRY": ("Retry Robotics Corp.", 17.5),
    "PREMIUM": ("Premium Holdings plc", 88.8),
}
SYNTHETIC = re.compile(r"^T(\d+)$")
FMP_FILES = {
    "quote": "quote",
    "profile": "profile",
    "ratios-ttm": "ratios_ttm",
    "key-metrics-ttm": "key_metrics_ttm",
}
METRICS_ENDPOINTS = {"ratios-ttm", "key-metrics-ttm"}

app = FastAPI(title="Fake data providers")
calls: Counter[str] = Counter()
call_times: list[float] = []
attempts: Counter[tuple[str, str]] = Counter()


def _fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _company(symbol: str) -> tuple[str, float] | None:
    if symbol in COMPANIES:
        return COMPANIES[symbol]
    if match := SYNTHETIC.match(symbol):
        n = int(match.group(1))
        return f"Test Company {n} Inc.", 100.0 + n
    return None


def _fmp_payload(endpoint: str, symbol: str) -> list[dict[str, Any]]:
    company = _company(symbol)
    if company is None:
        return []
    name, price = company
    data = dict(_fixture(f"fmp/{FMP_FILES[endpoint]}.json")[0], symbol=symbol)
    if endpoint == "quote":
        data.update(name=name, price=price)
    elif endpoint == "profile":
        data.update(companyName=name, price=price)
    return [data]


@app.middleware("http")
async def record_calls(request: Request, call_next: Any) -> Response:
    if not request.url.path.startswith("/_"):
        calls[request.url.path] += 1
        if request.url.path.startswith("/stable"):
            call_times.append(time.monotonic())
    response: Response = await call_next(request)
    return response


@app.get("/stable/{endpoint}")
async def fmp(endpoint: str, symbol: str = "", apikey: str = "") -> Response:
    if apikey != FMP_KEY:
        return JSONResponse(
            {"Error Message": "Invalid API KEY. Feel free to create a Free API Key."}, 401
        )
    if endpoint not in FMP_FILES:
        return JSONResponse({"Error Message": "Unknown endpoint"}, 404)

    attempts[(endpoint, symbol)] += 1
    attempt = attempts[(endpoint, symbol)]
    limit_reach = {"Error Message": "Limit Reach . Please upgrade your plan."}

    if symbol == "DOWN" or (symbol == "FLAKY" and attempt <= 2):
        return Response("Service Unavailable", 503)
    if symbol == "SLOW":
        await asyncio.sleep(3)
    if symbol == "LIMIT":
        return JSONResponse(limit_reach, 429)
    if symbol == "RETRY" and attempt == 1:
        return JSONResponse(limit_reach, 429, headers={"Retry-After": "1"})
    if symbol == "LIMIT200":
        return JSONResponse(limit_reach, 200)
    if symbol == "GARBAGE":
        return HTMLResponse("<html><body>502 Bad Gateway</body></html>", 200)
    if symbol == "SHAPE":
        return JSONResponse({"unexpected": "object"})
    if symbol == "NOPRICE":
        return JSONResponse([{"symbol": "NOPRICE", "name": "No Price Ltd"}])
    if symbol == "PREMIUM" and endpoint in METRICS_ENDPOINTS:
        return JSONResponse({"Error Message": "Restricted Endpoint: upgrade your plan."}, 402)
    return JSONResponse(_fmp_payload(endpoint, symbol))


@app.get("/v2/everything")
async def everything(q: str = "", x_api_key: str = Header(default="")) -> Response:
    if x_api_key != NEWS_KEY:
        return JSONResponse(
            {"status": "error", "code": "apiKeyInvalid", "message": "Your API key is invalid."},
            401,
        )
    if "DOWN" in q:
        return JSONResponse(
            {"status": "error", "code": "unexpectedError", "message": "Server error."}, 500
        )
    if "LIMIT" in q:
        body = {"status": "error", "code": "rateLimited", "message": "You have been rate limited."}
        return JSONResponse(body, 429)

    terms = re.findall(r'"([^"]+)"', q)
    subject = terms[0] if terms else "Company"
    payload = json.loads(
        (FIXTURES / "newsapi/everything.json").read_text().replace("Microsoft", subject)
    )
    return JSONResponse(payload)


@app.get("/_stats")
async def stats() -> dict[str, Any]:
    # Highest number of FMP calls seen in any rolling one-second window.
    times = sorted(call_times)
    peak, start = 0, 0
    for end, t in enumerate(times):
        while t - times[start] >= 1.0:
            start += 1
        peak = max(peak, end - start + 1)
    return {"calls": dict(calls), "total": sum(calls.values()), "fmp_peak_per_second": peak}


@app.post("/_reset")
async def reset() -> dict[str, str]:
    calls.clear()
    call_times.clear()
    attempts.clear()
    return {"status": "reset"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
