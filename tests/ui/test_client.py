from __future__ import annotations

import httpx
import pytest

from marketlens.ui.client import ApiError, MarketLensClient


def _client(handler: httpx.MockTransport) -> MarketLensClient:
    return MarketLensClient("http://api.test", transport=handler)


def test_readiness_returns_json() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "ok"}))

    assert _client(transport).readiness() == {"status": "ok"}


def test_analyze_posts_ticker() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"report_markdown": "# Report"})

    result = _client(httpx.MockTransport(handler)).analyze("MSFT")

    assert result == {"report_markdown": "# Report"}
    assert seen[0].url.path == "/api/v1/analysis"
    assert seen[0].content == b'{"ticker":"MSFT"}'


def test_error_envelope_is_decoded() -> None:
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            501,
            json={"error": {"code": "not_implemented", "message": "Later"}},
            headers={"X-Request-ID": "rid"},
        )
    )

    with pytest.raises(ApiError) as exc_info:
        _client(transport).analyze("MSFT")

    assert exc_info.value.status_code == 501
    assert exc_info.value.code == "not_implemented"
    assert exc_info.value.request_id == "rid"


def test_non_json_error_is_handled() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(502, text="Bad Gateway"))

    with pytest.raises(ApiError) as exc_info:
        _client(transport).readiness()

    assert exc_info.value.code == "http_error"
    assert exc_info.value.message == "Bad Gateway"


def test_connection_failure_becomes_api_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ApiError) as exc_info:
        _client(httpx.MockTransport(handler)).readiness()

    assert exc_info.value.code == "api_unreachable"
