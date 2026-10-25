"""Client-side rate limiting.

A token bucket per provider smooths bursts — e.g. agents fanning out several calls at
once — so we stay under the provider's per-minute limit instead of discovering it
through 429s. Daily plan quotas are enforced upstream and surface as
``ProviderRateLimitError``.

The bucket lives in process memory: with N workers the effective ceiling is N times the
configured rate. Move to a shared limiter (e.g. Azure Cache for Redis) if that matters.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

SleepFn = Callable[[float], Awaitable[None]]


class AsyncTokenBucket:
    def __init__(
        self,
        rate_per_minute: float,
        *,
        burst: int = 5,
        clock: Callable[[], float] = time.monotonic,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self._rate_per_second = rate_per_minute / 60
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        # Waiters queue on the lock, so tokens are handed out in arrival order.
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """Take one token, waiting if necessary. Returns the seconds spent waiting."""
        waited = 0.0
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return waited
                delay = (1 - self._tokens) / self._rate_per_second
                await self._sleep(delay)
                waited += delay

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._updated
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate_per_second)
        self._updated = now
