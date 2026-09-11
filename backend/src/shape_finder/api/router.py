from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from shape_finder import __version__
from shape_finder.api.dependencies import get_market_data_service
from shape_finder.api.schemas import HealthResponse, PriceBarResponse, TimeSeriesResponse
from shape_finder.application.market_data_service import MarketDataService
from shape_finder.core.market_data import BarInterval

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
