from __future__ import annotations

from dataclasses import dataclass

from marketlens.core.config import AgentSettings
from marketlens.llm import LLMClient
from marketlens.services.protocols import MarketDataProvider, NewsProvider


@dataclass(frozen=True)
class AgentDependencies:
    """Everything nodes need from the outside world, injected once when the graph is built.

    Providers are optional: a missing provider degrades the briefing, it does not
    prevent one.
    """

    llm: LLMClient
    market_data: MarketDataProvider | None
    news: NewsProvider | None
    settings: AgentSettings
