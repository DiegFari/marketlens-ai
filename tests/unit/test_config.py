from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from marketlens.core.config import Settings, get_settings


def test_defaults_are_safe_for_local_development() -> None:
    settings = Settings()

    assert settings.environment == "local"
    assert settings.debug is False
    assert settings.llm.provider == "groq"
    assert settings.llm_is_configured is False
    assert not any(settings.data_providers.configured_providers().values())


@pytest.mark.parametrize(
    ("env", "provider", "configured"),
    [
        ({"GROQ_API_KEY": "gsk"}, "groq", True),
        ({"LLM_PROVIDER": "ollama"}, "ollama", True),
        ({"LLM_PROVIDER": "azure_openai"}, "azure_openai", False),
    ],
)
def test_llm_provider_selection(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], provider: str, configured: bool
) -> None:
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    settings = Settings()

    assert settings.llm.provider == provider
    assert settings.llm_is_configured is configured


def test_reads_values_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com")
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "secret-key")
    monkeypatch.setenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "chat")
    monkeypatch.setenv("FMP_API_KEY", "fmp-key")
    monkeypatch.setenv("DATA_PROVIDER_MAX_RETRIES", "5")

    settings = Settings()

    assert settings.environment == "staging"
    assert settings.log_level == "DEBUG"
    assert settings.azure_openai.is_configured is True
    assert settings.data_providers.configured_providers()["financial_modeling_prep"] is True
    assert settings.data_providers.data_provider_max_retries == 5


def test_reads_values_from_dotenv_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("ENVIRONMENT=dev\nNEWSAPI_API_KEY=abc\n")

    settings = Settings()

    assert settings.environment == "dev"
    assert settings.data_providers.configured_providers()["newsapi"] is True


def test_secrets_are_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "super-secret")

    settings = Settings()

    assert "super-secret" not in repr(settings)
    assert "super-secret" not in str(settings.model_dump())
    assert settings.azure_openai.api_key is not None
    assert settings.azure_openai.api_key.get_secret_value() == "super-secret"


def test_cors_origins_accept_comma_separated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "https://a.example.com, https://b.example.com,")

    assert Settings().cors_origins == ["https://a.example.com", "https://b.example.com"]


def test_debug_is_rejected_in_production() -> None:
    with pytest.raises(ValidationError, match="DEBUG must be disabled"):
        Settings(environment="prod", debug=True)


def test_invalid_environment_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(environment="production")  # type: ignore[arg-type]


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_blank_values_count_as_unset(tmp_path: Path) -> None:
    # Regression: a copied .env.example ("GROQ_API_KEY=") looked configured with "".
    (tmp_path / ".env").write_text("GROQ_API_KEY=\nFMP_API_KEY=\nNEWSAPI_API_KEY=   \n")

    settings = Settings()

    assert settings.groq.api_key is None
    assert settings.llm_is_configured is False
    assert settings.data_providers.fmp_api_key is None
