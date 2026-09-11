from fastapi import Request

from shape_finder.application.market_data_service import MarketDataService
from shape_finder.core.market_data import MarketDataProvider


def get_market_data_service(request: Request) -> MarketDataService:
    provider: MarketDataProvider = request.app.state.market_data_provider
    return MarketDataService(provider)
