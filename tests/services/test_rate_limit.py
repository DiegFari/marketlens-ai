from __future__ import annotations

import pytest

from marketlens.services.rate_limit import AsyncTokenBucket

pytestmark = pytest.mark.anyio


class FakeClock:
    """A clock that only moves when the limiter sleeps or the test advances it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _bucket(clock: FakeClock, rate_per_minute: float = 60, burst: int = 2) -> AsyncTokenBucket:
    return AsyncTokenBucket(rate_per_minute, burst=burst, clock=clock, sleep=clock.sleep)


async def test_burst_is_served_without_waiting() -> None:
    clock = FakeClock()
    bucket = _bucket(clock, burst=3)

    waits = [await bucket.acquire() for _ in range(3)]

    assert waits == [0.0, 0.0, 0.0]
    assert clock.sleeps == []


async def test_waits_for_refill_once_burst_is_spent() -> None:
    clock = FakeClock()
    bucket = _bucket(clock, rate_per_minute=60, burst=1)  # one token per second

    await bucket.acquire()
    waited = await bucket.acquire()

    assert waited == pytest.approx(1.0)
    assert clock.now == pytest.approx(1.0)


async def test_tokens_refill_over_time_up_to_capacity() -> None:
    clock = FakeClock()
    bucket = _bucket(clock, rate_per_minute=60, burst=2)
    await bucket.acquire()
    await bucket.acquire()

    clock.now += 100  # long idle period must not bank more than `burst` tokens
    waits = [await bucket.acquire() for _ in range(3)]

    assert waits[:2] == [0.0, 0.0]
    assert waits[2] == pytest.approx(1.0)


@pytest.mark.parametrize(("rate", "burst"), [(0, 1), (-5, 1), (60, 0)])
def test_rejects_invalid_configuration(rate: float, burst: int) -> None:
    with pytest.raises(ValueError, match="must be"):
        AsyncTokenBucket(rate, burst=burst)
