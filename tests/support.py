"""Shared test helpers: recorded fixtures, a scripted HTTP transport and a fake sleep."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


class FakeSleep:
    """Records requested delays instead of sleeping."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class MockAPI:
    """Per-path response queues for ``httpx.MockTransport``; records every request.

    Each path serves its queued responses in order and repeats the last one.
    """

    def __init__(self) -> None:
        self._routes: dict[str, list[httpx.Response | Exception]] = {}
        self.requests: list[httpx.Request] = []

    def add(self, path: str, *responses: httpx.Response | Exception) -> MockAPI:
        self._routes[path] = list(responses)
        return self

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        queue = self._routes.get(request.url.path)
        if not queue:
            return httpx.Response(404, json={"message": f"no mock for {request.url.path}"})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)
