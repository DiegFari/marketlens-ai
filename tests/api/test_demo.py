from __future__ import annotations

import asyncio
from datetime import date

import pytest

from marketlens.api.demo import DemoGuard, DemoLimitError
from marketlens.core.config import DemoSettings

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.day = date(2026, 11, 5)

    def __call__(self) -> float:
        return self.now


def _guard(clock: Clock, **settings: object) -> DemoGuard:
    return DemoGuard(
        DemoSettings(**settings),  # type: ignore[arg-type]
        clock=clock,
        today=lambda: clock.day,
    )


def test_cache_expires_after_ttl() -> None:
    clock = Clock()
    guard = _guard(clock, cache_ttl_minutes=10)

    guard.store("MSFT", "briefing")
    assert guard.cached("MSFT") == "briefing"

    clock.now += 601
    assert guard.cached("MSFT") is None


def test_zero_ttl_disables_cache() -> None:
    guard = _guard(Clock(), cache_ttl_minutes=0)
    guard.store("MSFT", "briefing")
    assert guard.cached("MSFT") is None


def test_client_limit_is_per_client_and_hourly() -> None:
    clock = Clock()
    guard = _guard(clock, limits_enabled=True, hourly_requests_per_client=2)

    guard.charge_client("a")
    guard.charge_client("a")
    guard.charge_client("b")  # other visitors are unaffected
    with pytest.raises(DemoLimitError) as exc_info:
        guard.charge_client("a")
    assert exc_info.value.code == "client_limit_reached"

    clock.now += 3600
    guard.charge_client("a")  # window has moved on


def test_limits_disabled_by_default() -> None:
    guard = _guard(Clock(), hourly_requests_per_client=0, daily_briefings=0)
    guard.charge_client("a")
    assert guard.remaining_today is None


async def test_daily_limit_resets_next_day() -> None:
    clock = Clock()
    guard = _guard(clock, limits_enabled=True, daily_briefings=1)

    await guard.acquire_briefing()
    await guard.release_briefing()
    assert guard.remaining_today == 0
    with pytest.raises(DemoLimitError) as exc_info:
        await guard.acquire_briefing()
    assert exc_info.value.code == "daily_limit_reached"

    clock.day = date(2026, 11, 6)
    assert guard.remaining_today == 1
    await guard.acquire_briefing()


async def test_second_briefing_waits_for_the_first() -> None:
    guard = _guard(Clock(), max_concurrent_briefings=1, queue_timeout_seconds=5)
    await guard.acquire_briefing()
    assert guard.is_busy

    waiter = asyncio.create_task(guard.acquire_briefing())
    await asyncio.sleep(0.01)
    assert not waiter.done()

    await guard.release_briefing()
    await asyncio.wait_for(waiter, 1)
    assert guard.is_busy


async def test_queue_times_out_as_busy() -> None:
    guard = _guard(Clock(), max_concurrent_briefings=1, queue_timeout_seconds=0.05)
    await guard.acquire_briefing()

    with pytest.raises(DemoLimitError) as exc_info:
        await guard.acquire_briefing()
    assert exc_info.value.code == "busy"
