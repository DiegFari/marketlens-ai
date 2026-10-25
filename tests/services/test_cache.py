from __future__ import annotations

import pytest

from marketlens.services.cache import TTLCache


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_returns_value_until_ttl_expires() -> None:
    clock = Clock()
    cache = TTLCache(clock=clock)
    cache.set("k", {"v": 1}, ttl_seconds=10)

    clock.now = 9.9
    assert cache.get("k") == {"v": 1}

    clock.now = 10.0
    assert cache.get("k") is None
    assert len(cache) == 0


def test_evicts_least_recently_used_entry() -> None:
    cache = TTLCache(maxsize=2, clock=Clock())
    cache.set("a", 1, 60)
    cache.set("b", 2, 60)
    cache.get("a")  # "b" is now the least recently used

    cache.set("c", 3, 60)

    assert cache.get("a") == 1
    assert cache.get("b") is None
    assert cache.get("c") == 3


def test_missing_key_returns_none() -> None:
    assert TTLCache().get("nope") is None


def test_rejects_invalid_maxsize() -> None:
    with pytest.raises(ValueError, match="maxsize"):
        TTLCache(maxsize=0)
