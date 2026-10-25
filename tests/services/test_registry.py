from __future__ import annotations

import pytest

from marketlens.core.config import DataProviderSettings
from marketlens.services.fmp import FMPClient
from marketlens.services.newsapi import NewsAPIClient
from marketlens.services.registry import DataProviders

pytestmark = pytest.mark.anyio


async def test_no_keys_means_no_clients() -> None:
    providers = DataProviders.from_settings(DataProviderSettings())

    assert providers.market_data is None
    assert providers.news is None
    await providers.aclose()  # must be a no-op


async def test_builds_clients_for_configured_providers() -> None:
    settings = DataProviderSettings(fmp_api_key="fmp", newsapi_api_key="news")  # type: ignore[arg-type]

    providers = DataProviders.from_settings(settings)

    assert isinstance(providers.market_data, FMPClient)
    assert isinstance(providers.news, NewsAPIClient)
    await providers.aclose()
