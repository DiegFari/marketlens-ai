from __future__ import annotations

import pytest

from marketlens.ui.config import UISettings


def test_ui_defaults_point_at_local_api() -> None:
    assert UISettings().api_base_url == "http://localhost:8000"


def test_ui_reads_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UI_API_BASE_URL", "https://api.example.com")
    monkeypatch.setenv("UI_REQUEST_TIMEOUT_SECONDS", "30")

    settings = UISettings()

    assert settings.api_base_url == "https://api.example.com"
    assert settings.request_timeout_seconds == 30
