"""FastAPI dependencies shared across routers."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from marketlens.agents import BriefingWorkflow
from marketlens.core.config import Settings
from marketlens.services.errors import ProviderNotConfiguredError
from marketlens.services.protocols import MarketDataProvider, NewsProvider
from marketlens.services.registry import DataProviders


def get_app_settings(request: Request) -> Settings:
    """Settings bound to this app instance (lets tests inject their own)."""
    settings: Settings = request.app.state.settings
    return settings


def get_providers(request: Request) -> DataProviders:
    providers: DataProviders = request.app.state.providers
    return providers


def get_market_data(request: Request) -> MarketDataProvider:
    provider = get_providers(request).market_data
    if provider is None:
        raise ProviderNotConfiguredError("financial_modeling_prep", "FMP_API_KEY is not set")
    return provider


def get_news(request: Request) -> NewsProvider:
    provider = get_providers(request).news
    if provider is None:
        raise ProviderNotConfiguredError("newsapi", "NEWSAPI_API_KEY is not set")
    return provider


def get_workflow(request: Request) -> BriefingWorkflow | None:
    """None when Azure OpenAI is not configured; the route turns that into a 503."""
    workflow: BriefingWorkflow | None = request.app.state.workflow
    return workflow


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
WorkflowDep = Annotated[BriefingWorkflow | None, Depends(get_workflow)]
ProvidersDep = Annotated[DataProviders, Depends(get_providers)]
MarketDataDep = Annotated[MarketDataProvider, Depends(get_market_data)]
NewsDep = Annotated[NewsProvider, Depends(get_news)]
