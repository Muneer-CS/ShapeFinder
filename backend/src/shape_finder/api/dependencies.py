from fastapi import Request

from shape_finder.application.market_data_service import MarketDataService
from shape_finder.application.similarity_search import SimilaritySearchService
from shape_finder.application.universe import UniverseService


def get_market_data_service(request: Request) -> MarketDataService:
    service: MarketDataService = request.app.state.market_data_service
    return service


def get_similarity_search_service(request: Request) -> SimilaritySearchService:
    service: SimilaritySearchService = request.app.state.similarity_search_service
    return service


def get_universe_service(request: Request) -> UniverseService:
    service: UniverseService = request.app.state.universe_service
    return service
