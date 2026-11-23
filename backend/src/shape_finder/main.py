import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import cast

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response

from shape_finder import __version__
from shape_finder.api.error_handlers import register_error_handlers
from shape_finder.api.owner_access import AUTH_PATH, HEALTH_PATH, OwnerAccessMiddleware
from shape_finder.api.pattern_routes import router as pattern_router
from shape_finder.api.router import api_router
from shape_finder.api.safety import (
    RequestSafetyMiddleware,
    SearchAdmissionController,
    configure_logging,
)
from shape_finder.application.manual_pattern_scan import ManualPatternScanService
from shape_finder.application.market_data_service import MarketDataService
from shape_finder.application.massive_schedule import schedule_massive_refresh
from shape_finder.application.similarity_engine import ChartSimilarityEngine
from shape_finder.application.similarity_search import (
    HistoricalSimilarityScanner,
    SimilaritySearchService,
)
from shape_finder.application.universe import UniverseService
from shape_finder.application.universe_hydration import UniverseHydrationService
from shape_finder.config import Settings, get_settings
from shape_finder.core.market_data import MarketDataProvider
from shape_finder.core.persistence import MarketDataRepository
from shape_finder.core.universe import UniverseProvider, UniverseRepository
from shape_finder.infrastructure.market_data.twelve_data import TwelveDataProvider
from shape_finder.infrastructure.persistence.current_data_store import CurrentDataStore
from shape_finder.infrastructure.persistence.sqlite_market_data import SQLiteMarketDataRepository


def create_app(
    provider: MarketDataProvider | None = None,
    repository: MarketDataRepository | None = None,
    *,
    settings: Settings | None = None,
    current_scan_service: ManualPatternScanService | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    configure_logging(active_settings.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        active_repository = repository or SQLiteMarketDataRepository(active_settings.database_path)
        await active_repository.initialize()
        application.state.repository = active_repository
        application.state.current_pattern_service = current_scan_service
        if current_scan_service is None and isinstance(
            active_repository, SQLiteMarketDataRepository
        ):
            application.state.current_pattern_service = ManualPatternScanService(
                active_settings, CurrentDataStore(active_repository.path)
            )

        def configure_services(active_provider: MarketDataProvider) -> None:
            market_data = MarketDataService(active_provider, active_repository)
            universe_service = UniverseService(
                cast(UniverseProvider, active_provider),
                cast(UniverseRepository, active_repository),
                ttl=timedelta(hours=active_settings.universe_ttl_hours),
            )
            application.state.market_data_service = market_data
            application.state.universe_service = universe_service
            hydration_service = UniverseHydrationService(
                market_data,
                active_repository,
                universe_service,
                max_symbols=active_settings.universe_hydration_max_symbols,
                intraday_max_symbols=active_settings.universe_hydration_intraday_max_symbols,
                timeout_seconds=active_settings.universe_hydration_timeout_seconds,
            )
            application.state.similarity_search_service = SimilaritySearchService(
                market_data,
                HistoricalSimilarityScanner(ChartSimilarityEngine()),
                active_repository,
                universe_service,
                hydration_service,
            )

        @asynccontextmanager
        async def running_services() -> AsyncIterator[None]:
            task = (
                asyncio.create_task(schedule_massive_refresh(active_settings))
                if active_settings.massive_refresh_enabled
                else None
            )
            try:
                yield
            finally:
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        if provider is not None:
            configure_services(provider)
            async with running_services():
                yield
            return

        async with httpx.AsyncClient(
            base_url=active_settings.twelve_data_base_url,
            timeout=httpx.Timeout(active_settings.market_data_timeout_seconds),
        ) as client:
            key = (
                active_settings.twelve_data_api_key.get_secret_value()
                if active_settings.twelve_data_api_key
                else None
            )
            active_provider = TwelveDataProvider(client, key)
            configure_services(active_provider)
            async with running_services():
                yield

    application = FastAPI(
        title="ShapeFinder API",
        description="Stock chart similarity service foundation",
        version=__version__,
        lifespan=lifespan,
    )
    application.state.search_admission = SearchAdmissionController(
        active_settings.max_concurrent_scans
    )
    application.state.scan_timeout_seconds = active_settings.scan_timeout_seconds
    application.add_middleware(
        RequestSafetyMiddleware,
        max_body_bytes=active_settings.max_request_body_bytes,
    )
    if active_settings.hosted:
        username = active_settings.shapefinder_owner_username
        password = active_settings.shapefinder_owner_password
        origin = active_settings.shapefinder_public_origin
        assert username is not None and password is not None and origin is not None
        application.add_middleware(
            OwnerAccessMiddleware,
            username=username.get_secret_value(),
            password=password.get_secret_value(),
            origin=origin,
        )

        @application.get(HEALTH_PATH, include_in_schema=False)
        async def hosted_health() -> PlainTextResponse:
            return PlainTextResponse("ok")

        @application.get(AUTH_PATH, include_in_schema=False)
        async def authenticated_owner() -> Response:
            return Response(status_code=204)
    else:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=active_settings.cors_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Accept", "Content-Type"],
        )
    application.include_router(api_router, prefix="/api/v1")
    application.include_router(pattern_router, prefix="/api/v1")
    register_error_handlers(application)
    return application


app = create_app()
