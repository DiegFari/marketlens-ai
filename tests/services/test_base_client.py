from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from marketlens.services.base import BaseProviderClient, RetryPolicy, parse_retry_after
from marketlens.services.cache import TTLCache
from marketlens.services.errors import (
    ProviderAuthError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from tests.support import FakeSleep, MockAPI

pytestmark = pytest.mark.anyio


class DummyClient(BaseProviderClient):
    provider = "dummy"

    async def fetch(self, path: str = "/data", **params: Any) -> Any:
        return await self._get_json(path, params, cache_ttl=60)


class CountingLimiter:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self) -> float:
        self.calls += 1
        return 0.0


def _client(
    api: MockAPI, sleep: FakeSleep, *, max_retries: int = 2, cache: TTLCache | None = None
) -> DummyClient:
    return DummyClient(
        base_url="https://dummy.test",
        retry=RetryPolicy(max_retries=max_retries),
        cache=cache,
        transport=api.transport,
        sleep=sleep,
    )


OK = httpx.Response(200, json={"ok": True})


async def test_returns_json_on_success(mock_api: MockAPI, fake_sleep: FakeSleep) -> None:
    mock_api.add("/data", OK)

    assert await _client(mock_api, fake_sleep).fetch() == {"ok": True}


async def test_retries_transient_server_errors(mock_api: MockAPI, fake_sleep: FakeSleep) -> None:
    mock_api.add("/data", httpx.Response(503), OK)

    assert await _client(mock_api, fake_sleep).fetch() == {"ok": True}
    assert len(mock_api.requests) == 2
    assert len(fake_sleep.calls) == 1


async def test_gives_up_after_max_retries(mock_api: MockAPI, fake_sleep: FakeSleep) -> None:
    mock_api.add("/data", httpx.Response(502))

    with pytest.raises(ProviderUnavailableError) as exc_info:
        await _client(mock_api, fake_sleep, max_retries=2).fetch()

    assert exc_info.value.status_code == 502
    assert len(mock_api.requests) == 3  # 1 attempt + 2 retries
    assert len(fake_sleep.calls) == 2


async def test_backoff_delays_respect_ceiling(mock_api: MockAPI, fake_sleep: FakeSleep) -> None:
    mock_api.add("/data", httpx.Response(500))
    client = DummyClient(
        base_url="https://dummy.test",
        retry=RetryPolicy(max_retries=5, base_delay_seconds=1, max_delay_seconds=3),
        transport=mock_api.transport,
        sleep=fake_sleep,
    )

    with pytest.raises(ProviderUnavailableError):
        await client.fetch()

    assert all(0 <= d <= 3 for d in fake_sleep.calls)


@pytest.mark.parametrize(
    "exc", [httpx.ReadTimeout("timed out"), httpx.ConnectError("connection refused")]
)
async def test_transport_failures_are_retried(
    mock_api: MockAPI, fake_sleep: FakeSleep, exc: Exception
) -> None:
    mock_api.add("/data", exc, OK)

    assert await _client(mock_api, fake_sleep).fetch() == {"ok": True}
    assert len(fake_sleep.calls) == 1


async def test_rate_limit_honours_retry_after(mock_api: MockAPI, fake_sleep: FakeSleep) -> None:
    mock_api.add("/data", httpx.Response(429, headers={"Retry-After": "2"}), OK)

    assert await _client(mock_api, fake_sleep).fetch() == {"ok": True}
    assert fake_sleep.calls == [2.0]


async def test_rate_limit_without_hint_fails_fast(mock_api: MockAPI, fake_sleep: FakeSleep) -> None:
    mock_api.add("/data", httpx.Response(429, json={"message": "quota exceeded"}))

    with pytest.raises(ProviderRateLimitError) as exc_info:
        await _client(mock_api, fake_sleep).fetch()

    assert exc_info.value.retry_after is None
    assert exc_info.value.message == "quota exceeded"
    assert len(mock_api.requests) == 1
    assert fake_sleep.calls == []


async def test_long_retry_after_is_not_waited_for(mock_api: MockAPI, fake_sleep: FakeSleep) -> None:
    mock_api.add("/data", httpx.Response(429, headers={"Retry-After": "3600"}))

    with pytest.raises(ProviderRateLimitError) as exc_info:
        await _client(mock_api, fake_sleep).fetch()

    assert exc_info.value.retry_after == 3600
    assert fake_sleep.calls == []


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, ProviderAuthError),
        (403, ProviderAuthError),
        (402, ProviderPermissionError),
        (400, ProviderRequestError),
        (404, ProviderRequestError),
    ],
)
async def test_client_errors_are_typed_and_not_retried(
    mock_api: MockAPI, fake_sleep: FakeSleep, status: int, error_type: type[Exception]
) -> None:
    mock_api.add("/data", httpx.Response(status))

    with pytest.raises(error_type):
        await _client(mock_api, fake_sleep).fetch()

    assert len(mock_api.requests) == 1


async def test_non_json_success_is_a_response_error(
    mock_api: MockAPI, fake_sleep: FakeSleep
) -> None:
    mock_api.add("/data", httpx.Response(200, text="<html>maintenance</html>"))

    with pytest.raises(ProviderResponseError):
        await _client(mock_api, fake_sleep).fetch()


async def test_cache_serves_repeat_requests(mock_api: MockAPI, fake_sleep: FakeSleep) -> None:
    mock_api.add("/data", OK)
    client = _client(mock_api, fake_sleep, cache=TTLCache())

    await client.fetch(symbol="MSFT")
    await client.fetch(symbol="MSFT")
    await client.fetch(symbol="AAPL")

    assert len(mock_api.requests) == 2


async def test_errors_are_not_cached(mock_api: MockAPI, fake_sleep: FakeSleep) -> None:
    mock_api.add("/data", httpx.Response(400), OK)
    client = _client(mock_api, fake_sleep, cache=TTLCache())

    with pytest.raises(ProviderRequestError):
        await client.fetch()

    assert await client.fetch() == {"ok": True}


async def test_rate_limiter_is_consulted_on_every_attempt(
    mock_api: MockAPI, fake_sleep: FakeSleep
) -> None:
    mock_api.add("/data", httpx.Response(503), OK)
    limiter = CountingLimiter()
    client = DummyClient(
        base_url="https://dummy.test",
        rate_limiter=limiter,  # type: ignore[arg-type]
        transport=mock_api.transport,
        sleep=fake_sleep,
    )

    await client.fetch()

    assert limiter.calls == 2


async def test_concurrent_identical_requests_are_coalesced(
    mock_api: MockAPI, fake_sleep: FakeSleep
) -> None:
    # Regression: a burst on a cold key used to send one upstream call per caller.
    mock_api.add("/data", OK)
    client = _client(mock_api, fake_sleep)  # no cache: coalescing alone must dedupe

    results = await asyncio.gather(*(client.fetch(symbol="MSFT") for _ in range(20)))

    assert results == [{"ok": True}] * 20
    assert len(mock_api.requests) == 1


async def test_coalesced_failure_reaches_every_caller_and_is_not_sticky(
    mock_api: MockAPI, fake_sleep: FakeSleep
) -> None:
    mock_api.add("/data", httpx.Response(400), OK)
    client = _client(mock_api, fake_sleep)

    results = await asyncio.gather(client.fetch(), client.fetch(), return_exceptions=True)

    assert all(isinstance(r, ProviderRequestError) for r in results)
    assert await client.fetch() == {"ok": True}  # the in-flight entry was cleared
    assert len(mock_api.requests) == 2


async def test_rate_limiter_wait_is_bounded(mock_api: MockAPI) -> None:
    # Regression: requests used to queue behind the limiter for minutes under overload.
    class SaturatedLimiter:
        async def acquire(self) -> float:
            await asyncio.Event().wait()  # never gets capacity
            return 0.0

    mock_api.add("/data", OK)
    client = DummyClient(
        base_url="https://dummy.test",
        rate_limiter=SaturatedLimiter(),  # type: ignore[arg-type]
        rate_limit_max_wait_seconds=0.05,
        transport=mock_api.transport,
    )

    with pytest.raises(ProviderRateLimitError, match="client-side rate limit"):
        await client.fetch()

    assert mock_api.requests == []


async def test_async_context_manager_closes_client(mock_api: MockAPI) -> None:
    mock_api.add("/data", OK)

    async with DummyClient(base_url="https://dummy.test", transport=mock_api.transport) as client:
        await client.fetch()

    assert client._http.is_closed


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("5", 5.0),
        ("Sun, 25 Oct 2026 12:00:30 GMT", 30.0),
        ("Sun, 25 Oct 2026 11:00:00 GMT", 0.0),  # in the past
        ("soon", None),
        (None, None),
        ("", None),
    ],
)
def test_parse_retry_after(header: str | None, expected: float | None) -> None:
    now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)

    assert parse_retry_after(header, now=now) == expected
