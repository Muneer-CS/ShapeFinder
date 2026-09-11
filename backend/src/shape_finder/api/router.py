from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from shape_finder import __version__
from shape_finder.api.dependencies import (
    get_market_data_service,
    get_similarity_search_service,
)
from shape_finder.api.schemas import (
    HealthResponse,
    PriceBarResponse,
    ReferenceSummaryResponse,
    ScanStatisticsResponse,
    SimilarityComponentsResponse,
    SimilarityMatchResponse,
    SimilaritySearchRequest,
    SimilaritySearchResponse,
    TimeSeriesResponse,
)
from shape_finder.application.market_data_service import MarketDataService
from shape_finder.application.similarity_search import SimilaritySearchService
from shape_finder.core.market_data import BarInterval
from shape_finder.core.similarity_search import SimilaritySearchQuery

api_router = APIRouter()


@api_router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="shape-finder-api", version=__version__)


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
    service: Annotated[SimilaritySearchService, Depends(get_similarity_search_service)],
) -> SimilaritySearchResponse:
    result = await service.search(
        SimilaritySearchQuery(
            reference_symbol=request.reference.symbol,
            reference_start=request.reference.start,
            reference_end=request.reference.end,
            interval=request.reference.interval,
            search_start=request.search.start,
            search_end=request.search.end,
            candidate_symbols=tuple(request.search.symbols),
            top_n=request.top_n,
            minimum_similarity=request.minimum_similarity,
        )
    )
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
