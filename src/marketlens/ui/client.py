"""Thin HTTP client for the MarketLens API, used by the Streamlit app."""

from __future__ import annotations

from typing import Any

import httpx

from marketlens import __version__


class ApiError(Exception):
    """An error returned by (or while reaching) the API, decoded from its envelope."""

    def __init__(
        self, status_code: int, code: str, message: str, request_id: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id


class MarketLensClient:
    def __init__(
        self,
        base_url: str,
        timeout: float = 180.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"User-Agent": f"marketlens-ui/{__version__}"},
        )

    def readiness(self) -> dict[str, Any]:
        return self._request("GET", "/health/ready")

    def analyze(self, ticker: str) -> dict[str, Any]:
        return self._request("POST", "/api/v1/analysis", json={"ticker": ticker})

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.TimeoutException as exc:
            raise ApiError(504, "timeout", "The API did not respond in time.") from exc
        except httpx.TransportError as exc:
            raise ApiError(503, "api_unreachable", f"Cannot reach the API: {exc}") from exc

        if response.is_error:
            raise _decode_error(response)
        body: dict[str, Any] = response.json()
        return body


def _decode_error(response: httpx.Response) -> ApiError:
    request_id = response.headers.get("X-Request-ID")
    try:
        error = response.json()["error"]
        return ApiError(response.status_code, error["code"], error["message"], request_id)
    except (ValueError, KeyError, TypeError):
        return ApiError(
            response.status_code, "http_error", response.text[:200] or "Unknown error", request_id
        )
