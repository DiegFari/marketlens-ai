from __future__ import annotations

from fastapi.testclient import TestClient

from marketlens.api.main import create_app
from marketlens.core.config import Settings


def test_generates_request_id_when_absent(client: TestClient) -> None:
    response = client.get("/health/live")

    assert len(response.headers["X-Request-ID"]) == 32


def test_propagates_valid_incoming_request_id(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "abc-123"})

    assert response.headers["X-Request-ID"] == "abc-123"


def test_replaces_unsafe_incoming_request_id(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Request-ID": "bad id\nwith newline"})

    assert response.headers["X-Request-ID"] != "bad id\nwith newline"
    assert len(response.headers["X-Request-ID"]) == 32


def test_unhandled_exception_returns_error_envelope(settings: Settings) -> None:
    app = create_app(settings)

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    with TestClient(app) as client:
        response = client.get("/boom")

    error = response.json()["error"]
    assert response.status_code == 500
    assert error["code"] == "internal_error"
    assert "secret internal detail" not in response.text
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_wrong_method_uses_error_envelope_and_keeps_allow_header(client: TestClient) -> None:
    response = client.get("/api/v1/analysis")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert response.headers["Allow"] == "POST"


def test_cors_allows_configured_origin(client: TestClient) -> None:
    response = client.options(
        "/api/v1/analysis",
        headers={"Origin": "http://localhost:8501", "Access-Control-Request-Method": "POST"},
    )

    assert response.headers["access-control-allow-origin"] == "http://localhost:8501"
