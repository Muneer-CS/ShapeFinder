from fastapi import APIRouter

from shape_finder import __version__
from shape_finder.api.schemas import HealthResponse

api_router = APIRouter()


@api_router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", service="shape-finder-api", version=__version__)
