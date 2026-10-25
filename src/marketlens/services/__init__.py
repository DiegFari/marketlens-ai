"""Clients for external systems (market data, news; Azure services in later phases).

Layering::

    routes / agents  ->  protocols (MarketDataProvider, NewsProvider)
                     ->  FMPClient, NewsAPIClient
                     ->  BaseProviderClient (rate limit, retries, cache, error mapping)
                     ->  httpx
"""
