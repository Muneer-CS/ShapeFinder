import logging
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
    ReadinessError,
    ScanCapacityError,
    ScanTimeoutError,
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
logger = logging.getLogger("shape_finder.errors")


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "unavailable"))


def _content(code: str, message: str, request: Request) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "request_id": _request_id(request)}}


def _response(
    request: Request,
    status: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content=_content(code, message, request),
        headers={"X-Request-ID": _request_id(request), **(headers or {})},
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(InvalidSimilaritySearchError)
    async def similarity_search_error(
        request: Request, error: InvalidSimilaritySearchError
    ) -> JSONResponse:
        return _response(request, 422, "INVALID_SIMILARITY_SEARCH", str(error))

    @app.exception_handler(MarketDataError)
    async def market_data_error(request: Request, error: MarketDataError) -> JSONResponse:
        status, code, message = next(
            value for error_type, value in ERRORS.items() if isinstance(error, error_type)
        )
        logger.warning(
            "handled_provider_failure request_id=%s category=%s",
            _request_id(request),
            code,
        )
        return _response(request, status, code, message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError) -> JSONResponse:
        fields = sorted({str(item["loc"][-1]) for item in error.errors()})
        suffix = f" Invalid fields: {', '.join(fields)}." if fields else ""
        return _response(request, 422, "VALIDATION_ERROR", f"Request validation failed.{suffix}")

    @app.exception_handler(ScanCapacityError)
    async def capacity_error(request: Request, _: ScanCapacityError) -> JSONResponse:
        return _response(
            request,
            429,
            "SEARCH_CAPACITY_EXCEEDED",
            "Too many similarity searches are running. Try again shortly.",
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(ScanTimeoutError)
    async def timeout_error(request: Request, _: ScanTimeoutError) -> JSONResponse:
        return _response(
            request,
            504,
            "SEARCH_TIMEOUT",
            "The similarity search exceeded its execution time limit.",
        )

    @app.exception_handler(ReadinessError)
    async def readiness_error(request: Request, _: ReadinessError) -> JSONResponse:
        return _response(
            request,
            503,
            "NOT_READY",
            "The application is not ready to serve requests.",
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, error: Exception) -> JSONResponse:
        logger.error(
            "unexpected_error request_id=%s type=%s",
            _request_id(request),
            type(error).__name__,
        )
        return _response(
            request,
            500,
            "INTERNAL_SERVER_ERROR",
            "An unexpected server error occurred.",
        )
