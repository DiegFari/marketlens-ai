from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic import SecretStr

from marketlens import __version__
from marketlens.api.main import create_app
from marketlens.core.config import GroqSettings, Settings


def test_liveness(client: TestClient) -> None:
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_version_and_unconfigured_integrations(client: TestClient) -> None:
    response = client.get("/health/ready")

    body = response.json()
    assert response.status_code == 200
    assert body["version"] == __version__
    assert body["environment"] == "local"
    assert body["integrations"]["llm"] is False
    assert body["llm_provider"] == "groq"
    assert set(body["integrations"]) == {
        "llm",
        "blob_storage",
        "financial_modeling_prep",
        "newsapi",
    }


def test_readiness_reflects_configured_integration_without_leaking_secret() -> None:
    settings = Settings(groq=GroqSettings(api_key=SecretStr("top-secret")))
    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")

    assert response.json()["integrations"]["llm"] is True
    assert "top-secret" not in response.text


def test_openapi_schema_is_served(client: TestClient) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/analysis" in response.json()["paths"]
