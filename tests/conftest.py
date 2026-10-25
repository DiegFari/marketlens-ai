from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from marketlens.api.main import create_app
from marketlens.core.config import Settings, get_settings
from tests.support import FakeSleep, MockAPI

_ENV_PREFIXES = (
    "AZURE_",
    "FMP_",
    "NEWSAPI_",
    "DATA_PROVIDER_",
    "UI_",
    "LLM_",
    "GROQ_",
    "OLLAMA_",
    "AGENT_",
)
_ENV_NAMES = {"ENVIRONMENT", "DEBUG", "LOG_LEVEL", "LOG_FORMAT", "CORS_ORIGINS", "API_PREFIX"}


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Keep tests independent of the developer's shell and local .env file."""
    monkeypatch.chdir(tmp_path)  # no .env in an empty temp dir
    for name in list(os.environ):
        if name.startswith(_ENV_PREFIXES) or name in _ENV_NAMES:
            monkeypatch.delenv(name)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="local", log_format="json")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def mock_api() -> MockAPI:
    return MockAPI()


@pytest.fixture
def fake_sleep() -> FakeSleep:
    return FakeSleep()
