import asyncio
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request

from shape_finder import __version__
from shape_finder.api.dependencies import (
    get_market_data_service,
    get_similarity_search_service,
    get_universe_service,
)
from shape_finder.api.schemas import (
    HealthResponse,
    PriceBarResponse,
    ReadinessResponse,
    ReferenceSummaryResponse,
    ScanStatisticsResponse,
    SimilarityComponentsResponse,
    SimilarityMatchResponse,
    SimilaritySearchRequest,
    SimilaritySearchResponse,
    TimeSeriesResponse,
    UniverseListResponse,
    UniverseResponse,
)
from shape_finder.application.market_data_service import MarketDataService
from shape_finder.application.similarity_search import SimilaritySearchService
from shape_finder.application.universe import UniverseService
from shape_finder.core.errors import ReadinessError, ScanTimeoutError
from shape_finder.core.market_data import BarInterval
from shape_finder.core.similarity_search import SimilaritySearchQuery
from shape_finder.core.universe import UniverseSelection

api_router = APIRouter()


@api_router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    """Return process liveness without contacting the database or market-data provider."""
    return HealthResponse(status="ok", service="shape-finder-api", version=__version__)


@api_router.get("/readiness", response_model=ReadinessResponse, tags=["system"])
async def readiness(request: Request) -> ReadinessResponse:
    """Verify initialized local storage; provider availability is intentionally excluded."""
    repository = request.app.state.repository
    try:
        check = getattr(repository, "check_readiness", None)
        if check is not None:
            await check()
    except Exception as error:
        raise ReadinessError("Local storage is unavailable.") from error
    return ReadinessResponse(
        status="ready", service="shape-finder-api", version=__version__, database="ready"
    )


@api_router.get("/universes", response_model=UniverseListResponse, tags=["universes"])
async def universes(
    service: Annotated[UniverseService, Depends(get_universe_service)],
) -> UniverseListResponse:
    items = await service.list_universes()
    return UniverseListResponse(
        universes=[
            UniverseResponse(
                id=item.id.value,
                name=item.name,
                total_symbols=item.total_symbols,
                refreshed_at=item.refreshed_at,
                stale=item.stale,
            )
            for item in items
        ]
    )


@api_router.get(
    "/market-data/{symbol}",
    response_model=TimeSeriesResponse,
    tags=["market data"],
)
async def market_data(
    symbol: Annotated[str, Path(pattern=r"^[A-Za-z][A-Za-z0-9.\-]{0,14}$")],
    start: Annotated[datetime, Query(description="Inclusive timezone-aware start timestamp")],
    end: Annotated[datetime, Query(description="Inclusive timezone-aware end timestamp")],
    interval: Annotated[BarInterval, Query()],
    service: Annotated[MarketDataService, Depends(get_market_data_service)],
) -> TimeSeriesResponse:
    series = await service.get_time_series(symbol, start, end, interval)
    return TimeSeriesResponse(
        symbol=series.symbol,
        interval=series.interval.value,
        timezone=series.timezone,
        bars=[PriceBarResponse.model_validate(bar, from_attributes=True) for bar in series.bars],
    )


@api_router.post(
    "/similarity/search",
    response_model=SimilaritySearchResponse,
    tags=["similarity"],
)
async def similarity_search(
    request: SimilaritySearchRequest,
    http_request: Request,
    service: Annotated[SimilaritySearchService, Depends(get_similarity_search_service)],
) -> SimilaritySearchResponse:
    query = SimilaritySearchQuery(
        reference_symbol=request.reference.symbol,
        reference_start=request.reference.start,
        reference_end=request.reference.end,
        interval=request.reference.interval,
        search_start=request.search.start,
        search_end=request.search.end,
        candidate_symbols=tuple(
            request.search.symbols
            or (request.search.universe.symbols if request.search.universe else ())
        ),
        universe=(
            UniverseSelection(
                kind=request.search.universe.kind,
                symbols=tuple(request.search.universe.symbols),
            )
            if request.search.universe
            else None
        ),
        top_n=request.top_n,
        minimum_similarity=request.minimum_similarity,
    )
    controller = http_request.app.state.search_admission
    async with controller.admit() as lease:
        search_task = asyncio.create_task(service.search(query))
        try:
            result = await asyncio.wait_for(
                asyncio.shield(search_task),
                timeout=http_request.app.state.scan_timeout_seconds,
            )
        except TimeoutError as error:
            lease.defer_release_until(search_task)
            raise ScanTimeoutError("Similarity search timed out.") from error
        except asyncio.CancelledError:
            lease.defer_release_until(search_task)
            raise
    return SimilaritySearchResponse(
        reference=ReferenceSummaryResponse(
            symbol=result.reference.symbol,
            start=result.reference.start,
            end=result.reference.end,
            interval=result.reference.interval.value,
            bar_count=result.reference.bar_count,
        ),
        search_start=result.search_start,
        search_end=result.search_end,
        matches=[
            SimilarityMatchResponse(
                symbol=match.symbol,
                start=match.start,
                end=match.end,
                interval=match.interval.value,
                bar_count=match.bar_count,
                overall_score=match.score.overall_score,
                components=SimilarityComponentsResponse(
                    shape=match.score.shape_score,
                    direction=match.score.direction_score,
                    error=match.score.error_score,
                    amplitude=match.score.amplitude_score,
                ),
            )
            for match in result.matches
        ],
        statistics=ScanStatisticsResponse.model_validate(result.statistics, from_attributes=True),
    )
