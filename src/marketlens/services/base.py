"""Shared HTTP behaviour for every data provider client.

One place implements the resilience policy, so FMP, NewsAPI and any future provider
behave identically:

* client-side rate limiting (token bucket) before every attempt
* retries with exponential backoff and full jitter for transient failures
* 429 handling that honours ``Retry-After`` and fails fast when no hint is given
  (typically an exhausted daily quota, where waiting seconds would not help)
* response caching keyed by path + params, with identical concurrent requests
  coalesced into a single upstream call
* a bounded wait for rate-limiter capacity, so overload fails fast instead of queueing
* translation of HTTP/transport failures into typed ``DataProviderError`` subclasses

Secrets: API keys live in client-level params/headers and are never part of the
``path``/``params`` that are logged or used as cache keys.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Any, ClassVar, Self, TypeVar
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel, ValidationError

from marketlens import __version__
from marketlens.services.cache import TTLCache
from marketlens.services.errors import (
    DataProviderError,
    ProviderAuthError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderRequestError,
    ProviderResponseError,
    ProviderUnavailableError,
)
from marketlens.services.rate_limit import AsyncTokenBucket, SleepFn

logger = logging.getLogger("marketlens.providers")

ModelT = TypeVar("ModelT", bound=BaseModel)


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    # A Retry-After longer than this is treated as "not now" and raised immediately.
    max_retry_after_seconds: float = 30.0

    def backoff(self, attempt: int) -> float:
        """Full-jitter exponential backoff (spreads retries from concurrent callers)."""
        ceiling = min(self.max_delay_seconds, self.base_delay_seconds * 2**attempt)
        return random.uniform(0, ceiling)  # noqa: S311 (not cryptographic)


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Parse a Retry-After header given as seconds or as an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


class BaseProviderClient:
    provider: ClassVar[str]

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 15.0,
        retry: RetryPolicy | None = None,
        rate_limiter: AsyncTokenBucket | None = None,
        rate_limit_max_wait_seconds: float = 10.0,
        cache: TTLCache | None = None,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._retry = retry or RetryPolicy()
        self._rate_limiter = rate_limiter
        self._rate_limit_max_wait = rate_limit_max_wait_seconds
        self._cache = cache
        self._sleep = sleep
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            params=dict(params or {}),
            headers={"User-Agent": f"marketlens-ai/{__version__}", **(headers or {})},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ requests

    async def _get_json(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        cache_ttl: float | None = None,
    ) -> Any:
        key = f"{path}?{urlencode(sorted((params or {}).items()))}"
        if self._cache is not None and cache_ttl is not None:
            cached = self._cache.get(key)
            if cached is not None:
                logger.debug("provider cache hit", extra={"provider": self.provider, "path": path})
                return cached

        # Coalesce identical concurrent requests into one upstream call. Without this, a
        # burst on a cold cache key sends N identical calls, burns quota and queues them
        # all behind the rate limiter (a cache stampede).
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._fetch(path, params, key, cache_ttl))
            self._inflight[key] = task
            task.add_done_callback(lambda done: self._forget(key, done))
        else:
            logger.debug(
                "provider request coalesced", extra={"provider": self.provider, "path": path}
            )
        # shield: one caller disconnecting must not cancel the call others are awaiting.
        return await asyncio.shield(task)

    async def _fetch(
        self, path: str, params: Mapping[str, Any] | None, key: str, cache_ttl: float | None
    ) -> Any:
        payload = await self._get_with_retries(path, params)
        if self._cache is not None and cache_ttl is not None:
            self._cache.set(key, payload, cache_ttl)
        return payload

    def _forget(self, key: str, task: asyncio.Task[Any]) -> None:
        self._inflight.pop(key, None)
        if not task.cancelled():
            task.exception()  # mark as retrieved even if every waiter went away

    async def _get_with_retries(self, path: str, params: Mapping[str, Any] | None) -> Any:
        attempt = 0
        while True:
            try:
                return await self._attempt(path, params)
            except DataProviderError as exc:
                delay = self._retry_delay(exc, attempt)
                if delay is None:
                    raise
                attempt += 1
                logger.warning(
                    "provider request failed, retrying",
                    extra={
                        "provider": self.provider,
                        "path": path,
                        "attempt": attempt,
                        "delay_seconds": round(delay, 2),
                        "error": type(exc).__name__,
                    },
                )
                await self._sleep(delay)

    def _retry_delay(self, exc: DataProviderError, attempt: int) -> float | None:
        if attempt >= self._retry.max_retries:
            return None
        if isinstance(exc, ProviderRateLimitError):
            if exc.retry_after is None or exc.retry_after > self._retry.max_retry_after_seconds:
                return None
            return exc.retry_after
        if isinstance(exc, ProviderUnavailableError):
            return self._retry.backoff(attempt)
        return None

    async def _attempt(self, path: str, params: Mapping[str, Any] | None) -> Any:
        if self._rate_limiter is not None:
            # Bounded queue: under sustained overload, fail fast instead of letting
            # requests pile up behind the limiter for minutes.
            try:
                waited = await asyncio.wait_for(
                    self._rate_limiter.acquire(), self._rate_limit_max_wait
                )
            except TimeoutError:
                raise ProviderRateLimitError(
                    self.provider,
                    f"client-side rate limit: no capacity within {self._rate_limit_max_wait:g}s",
                ) from None
            if waited:
                logger.debug(
                    "rate limiter delayed request",
                    extra={"provider": self.provider, "waited_seconds": round(waited, 2)},
                )

        start = time.perf_counter()
        try:
            response = await self._http.get(path, params=params)
        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(self.provider, "request timed out") from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError(
                self.provider, f"connection failed ({type(exc).__name__})"
            ) from exc

        logger.info(
            "provider request",
            extra={
                "provider": self.provider,
                "path": path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 2),
            },
        )

        try:
            payload = response.json()
        except ValueError:
            payload = None

        error = self._error_for(response, payload)
        if error is not None:
            raise error
        if payload is None:
            raise ProviderResponseError(
                self.provider, "response body is not JSON", status_code=response.status_code
            )
        return payload

    # ------------------------------------------------------------ error mapping

    def _error_message(self, payload: Any) -> str | None:
        """Extract the provider's error text. Override for provider-specific shapes."""
        if isinstance(payload, dict):
            message = payload.get("message")
            return str(message) if message else None
        return None

    def _error_for(self, response: httpx.Response, payload: Any) -> DataProviderError | None:
        """Map a response to an error, or None on success. Override to add body checks."""
        status = response.status_code
        if response.is_success:
            return None

        message = self._error_message(payload) or response.reason_phrase or f"HTTP {status}"
        if status in (401, 403):
            return ProviderAuthError(self.provider, message, status_code=status)
        if status == 402:
            return ProviderPermissionError(self.provider, message, status_code=status)
        if status == 429:
            return ProviderRateLimitError(
                self.provider,
                message,
                retry_after=parse_retry_after(response.headers.get("Retry-After")),
                status_code=status,
            )
        if status >= 500:
            return ProviderUnavailableError(self.provider, message, status_code=status)
        return ProviderRequestError(self.provider, message, status_code=status)

    def _validate(self, model: type[ModelT], data: Any) -> ModelT:
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise ProviderResponseError(
                self.provider,
                f"unexpected {model.__name__} payload ({exc.error_count()} validation errors)",
            ) from exc
