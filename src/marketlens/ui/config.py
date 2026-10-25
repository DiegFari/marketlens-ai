"""UI settings. Kept separate from the API's so the UI container needs no API secrets."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from marketlens.core.config import EnvSettingsBase, LogFormat, LogLevel


class UISettings(EnvSettingsBase):
    model_config = SettingsConfigDict(env_prefix="UI_")

    api_base_url: str = "http://localhost:8000"
    # Agent runs can take a while; the UI should wait longer than a normal request.
    request_timeout_seconds: float = Field(default=180.0, gt=0)
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "console"


@lru_cache
def get_ui_settings() -> UISettings:
    return UISettings()
