from fastapi import Request

from shape_finder.application.market_data_service import MarketDataService


def get_market_data_service(request: Request) -> MarketDataService:
    service: MarketDataService = request.app.state.market_data_service
    return service
