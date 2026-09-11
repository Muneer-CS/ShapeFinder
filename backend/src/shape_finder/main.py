from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shape_finder import __version__
from shape_finder.api.router import api_router
from shape_finder.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="ShapeFinder API",
        description="Stock chart similarity service foundation",
        version=__version__,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET"],
        allow_headers=["Accept", "Content-Type"],
    )
    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_app()
