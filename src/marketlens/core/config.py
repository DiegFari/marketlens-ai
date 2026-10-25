"""Application configuration.

Every setting comes from environment variables (plus a local ``.env`` file during
development). On Azure the same variables are supplied as App Service application
settings, ideally as Key Vault references, so the code never changes between
environments.

Secrets are typed as ``SecretStr``: they are masked in ``repr()``, logs and
tracebacks and must be unwrapped explicitly with ``.get_secret_value()``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "dev", "staging", "prod"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
LogFormat = Literal["json", "console"]


class EnvSettingsBase(BaseSettings):
    """Common loading behaviour for every settings group."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # `GROQ_API_KEY=` (as copied from .env.example) means "not set", not "empty key".
        env_ignore_empty=True,
    )


LLMProvider = Literal["groq", "ollama", "azure_openai"]
StructuredOutputMethod = Literal["json_schema", "function_calling"]


class LLMSettings(EnvSettingsBase):
    """Which model provider the agents use, and how they call it.

    Switching provider is configuration only: ``LLM_PROVIDER=groq|ollama|azure_openai``.
    """

    model_config = SettingsConfigDict(env_prefix="LLM_")

    provider: LLMProvider = "groq"
    # How to request structured output. Unset = the provider's default (json_schema
    # for Azure OpenAI, function_calling for Groq and Ollama, which support it more
    # widely across models).
    structured_output: StructuredOutputMethod | None = None
    # If the primary method produces bad output (schema unsupported, invalid JSON, a
    # refusal, no function call), retry once with the other method.
    structured_output_fallback: bool = True
    # Unset by default: reasoning models reject any non-default temperature.
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    # SDK-level retries with backoff (honouring Retry-After) for 429/5xx.
    max_retries: int = Field(default=3, ge=0, le=8)


class GroqSettings(EnvSettingsBase):
    """Groq: free tier, OpenAI-compatible API. Used for the shareable demo."""

    model_config = SettingsConfigDict(env_prefix="GROQ_")

    api_key: SecretStr | None = None
    model: str = "openai/gpt-oss-120b"
    base_url: str = "https://api.groq.com/openai/v1"


class OllamaSettings(EnvSettingsBase):
    """Ollama: free local models through its OpenAI-compatible endpoint."""

    model_config = SettingsConfigDict(env_prefix="OLLAMA_")

    model: str = "qwen2.5:3b"
    base_url: str = "http://localhost:11434/v1"


class AzureOpenAISettings(EnvSettingsBase):
    """Azure OpenAI connection (``LLM_PROVIDER=azure_openai``)."""

    model_config = SettingsConfigDict(env_prefix="AZURE_OPENAI_")

    endpoint: str | None = None
    api_key: SecretStr | None = None
    # Must support structured outputs (json_schema): 2024-08-01-preview or later.
    api_version: str = "2024-10-21"
    # The deployment name is chosen when the model is deployed in Azure AI Foundry,
    # so there is deliberately no default.
    chat_deployment: str | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.endpoint and self.api_key and self.chat_deployment)


class AgentSettings(EnvSettingsBase):
    """Behaviour of the LangGraph briefing workflow."""

    model_config = SettingsConfigDict(env_prefix="AGENT_")

    # Hard ceiling for one briefing, end to end (data collection + all LLM calls).
    workflow_timeout_seconds: float = Field(default=120.0, gt=0)
    # Safety valve against routing loops; a normal run uses 4-5 supervisor turns.
    max_supervisor_turns: int = Field(default=8, ge=3, le=20)
    news_lookback_days: int = Field(default=7, ge=1, le=30)
    # Headlines handed to the News Research agent (it reads details via a tool).
    news_articles_for_analysis: int = Field(default=30, ge=5, le=50)
    news_max_tool_rounds: int = Field(default=3, ge=1, le=6)
    # Verbose DEBUG logs of graph updates, prompts and model outputs. Never in prod.
    debug: bool = False


class DataProviderSettings(EnvSettingsBase):
    """External market-data (Financial Modeling Prep) and news (NewsAPI) providers."""

    fmp_api_key: SecretStr | None = None
    fmp_base_url: str = "https://financialmodelingprep.com/stable"
    # Client-side ceiling to smooth bursts; daily plan quotas are enforced upstream.
    fmp_requests_per_minute: float = Field(default=300, gt=0)

    newsapi_api_key: SecretStr | None = None
    newsapi_base_url: str = "https://newsapi.org/v2"
    newsapi_requests_per_minute: float = Field(default=30, gt=0)
    # Comma-separated; sites whose listings match company names but carry no news.
    newsapi_exclude_domains: str = "pypi.org,rlsbb.cc"
    # Below this many headline matches, the search widens to article descriptions.
    newsapi_min_headline_articles: int = Field(default=10, ge=0, le=100)

    data_provider_timeout_seconds: float = Field(default=15.0, gt=0)
    data_provider_max_retries: int = Field(default=3, ge=0, le=10)
    # Longest a request may queue for rate-limiter capacity before failing fast.
    data_provider_rate_limit_max_wait_seconds: float = Field(default=10.0, gt=0)
    # In-process response cache; protects small free-tier daily quotas.
    data_provider_cache_enabled: bool = True

    def configured_providers(self) -> dict[str, bool]:
        return {
            "financial_modeling_prep": self.fmp_api_key is not None,
            "newsapi": self.newsapi_api_key is not None,
        }


class StorageSettings(EnvSettingsBase):
    """Azure Blob Storage, reserved for persisting generated briefings."""

    model_config = SettingsConfigDict(env_prefix="AZURE_STORAGE_")

    # Prefer account_url + managed identity in Azure; the connection string is for local
    # development against Azurite.
    account_url: str | None = None
    connection_string: SecretStr | None = None
    reports_container: str = "reports"

    @property
    def is_configured(self) -> bool:
        return bool(self.account_url or self.connection_string)


class Settings(EnvSettingsBase):
    """Top-level application settings."""

    app_name: str = "MarketLens AI"
    environment: Environment = "local"
    debug: bool = False
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "console"

    api_prefix: str = "/api/v1"
    # NoDecode lets operators write a plain comma-separated list instead of JSON.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:8501"]
    )

    llm: LLMSettings = Field(default_factory=LLMSettings)
    groq: GroqSettings = Field(default_factory=GroqSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    azure_openai: AzureOpenAISettings = Field(default_factory=AzureOpenAISettings)
    agents: AgentSettings = Field(default_factory=AgentSettings)
    data_providers: DataProviderSettings = Field(default_factory=DataProviderSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalise_log_level(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _guard_production(self) -> Settings:
        if self.environment == "prod" and self.debug:
            raise ValueError("DEBUG must be disabled when ENVIRONMENT=prod")
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "prod"

    @property
    def llm_is_configured(self) -> bool:
        """Whether the selected provider has what it needs to make calls."""
        match self.llm.provider:
            case "groq":
                return self.groq.api_key is not None
            case "azure_openai":
                return self.azure_openai.is_configured
            case "ollama":
                return True  # local, no credentials; reachability is checked per call


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings, loaded once on first use."""
    return Settings()
