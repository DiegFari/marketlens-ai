"""Quota protection for a public demo: a briefing cache, usage limits and a concurrency cap.

Everything is in-process memory, which fits the single-worker deployment this targets
(one small server). Counters reset on restart, which errs on the generous side.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from marketlens.core.config import DemoSettings

Clock = Callable[[], float]


class DemoLimitError(Exception):
    """A usage limit was reached. ``code`` is stable for clients."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DemoGuard:
    def __init__(
        self,
        settings: DemoSettings,
        *,
        clock: Clock = time.monotonic,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._today = today
        self._cache: dict[str, tuple[float, Any]] = {}
        self._clients: dict[str, deque[float]] = {}
        self._day = today()
        self._used_today = 0
        self._running = 0
        self._slots = asyncio.Condition()

    # ------------------------------------------------------------------ cache
    def cached(self, ticker: str) -> Any | None:
        entry = self._cache.get(ticker)
        if entry is None:
            return None
        expires, value = entry
        if self._clock() >= expires:
            del self._cache[ticker]
            return None
        return value

    def store(self, ticker: str, value: Any) -> None:
        ttl = self._settings.cache_ttl_minutes * 60
        if ttl > 0:
            self._cache[ticker] = (self._clock() + ttl, value)

    # ------------------------------------------------------------------ limits
    @property
    def remaining_today(self) -> int | None:
        if not self._settings.limits_enabled:
            return None
        self._roll_day()
        return max(self._settings.daily_briefings - self._used_today, 0)

    def charge_client(self, client: str) -> None:
        """Count one model-using request for ``client``; raise if over the hourly limit."""
        if not self._settings.limits_enabled:
            return
        window = self._clients.setdefault(client, deque())
        now = self._clock()
        while window and now - window[0] >= 3600:
            window.popleft()
        if len(window) >= self._settings.hourly_requests_per_client:
            minutes = max(1, round((3600 - (now - window[0])) / 60))
            raise DemoLimitError(
                "client_limit_reached",
                f"You've reached the demo limit for this hour. Try again in about "
                f"{minutes} minutes, or browse the example briefings.",
            )
        window.append(now)

    @property
    def is_busy(self) -> bool:
        return self._running >= self._settings.max_concurrent_briefings

    async def acquire_briefing(self) -> None:
        """Reserve a slot for a new briefing (daily cap, then wait for a free slot)."""
        async with self._slots:
            if self._settings.limits_enabled:
                self._roll_day()
                if self._used_today >= self._settings.daily_briefings:
                    raise DemoLimitError(
                        "daily_limit_reached",
                        "Today's live demo limit has been reached. Browse the example "
                        "briefings, or come back tomorrow.",
                    )
            try:
                await asyncio.wait_for(
                    self._slots.wait_for(lambda: not self.is_busy),
                    self._settings.queue_timeout_seconds,
                )
            except TimeoutError as exc:
                raise DemoLimitError(
                    "busy",
                    "The agents are busy with other briefings right now. Try again in a minute.",
                ) from exc
            self._running += 1
            if self._settings.limits_enabled:
                self._used_today += 1

    async def release_briefing(self) -> None:
        async with self._slots:
            self._running = max(self._running - 1, 0)
            self._slots.notify()

    def _roll_day(self) -> None:
        today = self._today()
        if today != self._day:
            self._day = today
            self._used_today = 0
