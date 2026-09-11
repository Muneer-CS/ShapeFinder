from typing import Final

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from shape_finder.core.errors import (
    AuthenticationError,
    InvalidDateRangeError,
    InvalidSymbolError,
    MalformedProviderResponseError,
    MarketDataError,
    MissingApiKeyError,
    NoDataError,
    ProviderNetworkError,
    RateLimitError,
    UnsupportedIntervalError,
)
from shape_finder.core.similarity_search import InvalidSimilaritySearchError

ERRORS: Final[dict[type[MarketDataError], tuple[int, str, str]]] = {
    MissingApiKeyError: (503, "PROVIDER_NOT_CONFIGURED", "Market data is not configured."),
    AuthenticationError: (
        502,
        "PROVIDER_AUTHENTICATION_FAILED",
        "Market-data authentication failed.",
    ),
    InvalidSymbolError: (404, "INVALID_SYMBOL", "The requested symbol was not found."),
    InvalidDateRangeError: (422, "INVALID_DATE_RANGE", "The requested date range is invalid."),
    UnsupportedIntervalError: (422, "UNSUPPORTED_INTERVAL", "The interval is not supported."),
    RateLimitError: (429, "PROVIDER_RATE_LIMITED", "The market-data rate limit was exceeded."),
    ProviderNetworkError: (502, "PROVIDER_UNAVAILABLE", "The market-data provider is unavailable."),
    MalformedProviderResponseError: (
        502,
        "MALFORMED_PROVIDER_RESPONSE",
        "The market-data provider returned an invalid response.",
    ),
    NoDataError: (404, "NO_DATA", "No market data exists for the requested period."),
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(InvalidSimilaritySearchError)
    async def similarity_search_error(
        _: Request, error: InvalidSimilaritySearchError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_SIMILARITY_SEARCH",
                    "message": str(error),
                }
            },
        )

    @app.exception_handler(MarketDataError)
    async def market_data_error(_: Request, error: MarketDataError) -> JSONResponse:
        status, code, message = ERRORS[type(error)]
        return JSONResponse(
            status_code=status, content={"error": {"code": code, "message": message}}
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
        fields = sorted({str(item["loc"][-1]) for item in error.errors()})
        suffix = f" Invalid fields: {', '.join(fields)}." if fields else ""
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": f"Request validation failed.{suffix}",
                }
            },
        )
