"""FastAPI application factory.

Run with::

    uvicorn marketlens.api.main:create_app --factory

A factory (instead of a module-level ``app``) means importing this module has no side
effects, and tests can build an app with their own ``Settings`` and providers.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from marketlens import __version__
from marketlens.agents import AgentDependencies, BriefingWorkflow
from marketlens.api.errors import register_exception_handlers
from marketlens.api.middleware import REQUEST_ID_HEADER, RequestContextMiddleware
from marketlens.api.routes import analysis, companies, health
from marketlens.core.config import Settings, get_settings
from marketlens.core.logging import configure_logging
from marketlens.llm import LLMClient, build_llm_client
from marketlens.services.registry import DataProviders

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    if app.state.providers is None:
        app.state.providers = DataProviders.from_settings(settings.data_providers)

    llm: LLMClient | None = app.state.llm
    if llm is None and settings.llm_is_configured:
        llm = build_llm_client(settings)
    app.state.workflow = (
        BriefingWorkflow(
            AgentDependencies(
                llm=llm,
                market_data=app.state.providers.market_data,
                news=app.state.providers.news,
                settings=settings.agents,
            )
        )
        if llm is not None
        else None
    )

    logger.info(
        "application starting",
        extra={
            "environment": settings.environment,
            "version": __version__,
            "providers": settings.data_providers.configured_providers(),
            "llm": llm.model_name if llm else None,
        },
    )
    try:
        yield
    finally:
        await app.state.providers.aclose()
        logger.info("application stopped")


def create_app(
    settings: Settings | None = None,
    providers: DataProviders | None = None,
    llm: LLMClient | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description="Multi-agent market intelligence briefings. Not investment advice.",
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = settings
    # Built in the lifespan unless injected, so HTTP clients bind to the server's loop.
    app.state.providers = providers
    app.state.llm = llm  # tests inject a fake; otherwise built from settings
    app.state.workflow = None

    # Middleware added last runs first: CORS wraps the request-context middleware.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER],
    )

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(analysis.router, prefix=settings.api_prefix)
    app.include_router(companies.router, prefix=settings.api_prefix)
    return app
