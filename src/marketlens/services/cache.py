"""Small in-process TTL + LRU cache for provider responses.

Free tiers allow a few hundred calls a day, and one report touches the same company
data several times, so caching is part of rate-limit handling, not just performance.
Per-process by design; a shared cache (Redis) can replace it behind the same interface.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any


class TTLCache:
    def __init__(self, maxsize: int = 512, clock: Callable[[], float] = time.monotonic) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be at least 1")
        self._maxsize = maxsize
        self._clock = clock
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= self._clock():
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        self._data[key] = (self._clock() + ttl_seconds, value)
        self._data.move_to_end(key)
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def __len__(self) -> int:
        return len(self._data)
