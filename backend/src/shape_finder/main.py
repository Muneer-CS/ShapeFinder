from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shape_finder import __version__
from shape_finder.api.error_handlers import register_error_handlers
from shape_finder.api.router import api_router
from shape_finder.config import get_settings
from shape_finder.core.market_data import MarketDataProvider
from shape_finder.infrastructure.market_data.twelve_data import TwelveDataProvider


def create_app(provider: MarketDataProvider | None = None) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if provider is not None:
            application.state.market_data_provider = provider
            yield
            return

        async with httpx.AsyncClient(
            base_url=settings.twelve_data_base_url,
            timeout=httpx.Timeout(settings.market_data_timeout_seconds),
        ) as client:
            key = (
                settings.twelve_data_api_key.get_secret_value()
                if settings.twelve_data_api_key
                else None
            )
            application.state.market_data_provider = TwelveDataProvider(client, key)
            yield

    application = FastAPI(
        title="ShapeFinder API",
        description="Stock chart similarity service foundation",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Accept", "Content-Type"],
    )
    application.include_router(api_router, prefix="/api/v1")
    register_error_handlers(application)
    return application


app = create_app()
