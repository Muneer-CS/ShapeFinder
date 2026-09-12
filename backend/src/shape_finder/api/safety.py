import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import Any, Final
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response
from starlette.types import ASGIApp

from shape_finder.core.errors import ScanCapacityError

REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")
_REQUEST_ID_PATTERN: Final = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
logger = logging.getLogger("shape_finder.api")


def configure_logging(level: str) -> None:
    """Configure concise process logging without request bodies or provider query strings."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s level=%(levelname)s logger=%(name)s message=%(message)s",
        force=True,
    )
    # httpx INFO includes fully rendered query strings; provider credentials use a query field.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


class RequestSafetyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        super().__init__(app)
        self._max_body_bytes = max_body_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get("x-request-id", "")
        request_id = supplied if _REQUEST_ID_PATTERN.fullmatch(supplied) else uuid4().hex
        request.state.request_id = request_id
        token = REQUEST_ID.set(request_id)
        started = perf_counter()
        try:
            if request.method in {"POST", "PUT", "PATCH"}:
                content_length = request.headers.get("content-length")
                if content_length:
                    try:
                        declared = int(content_length)
                    except ValueError:
                        return self._error(400, "INVALID_CONTENT_LENGTH", request_id)
                    if declared < 0:
                        return self._error(400, "INVALID_CONTENT_LENGTH", request_id)
                    if declared > self._max_body_bytes:
                        return self._error(413, "REQUEST_TOO_LARGE", request_id)
                body = await request.body()
                if len(body) > self._max_body_bytes:
                    return self._error(413, "REQUEST_TOO_LARGE", request_id)

            logger.info(
                "request_start request_id=%s method=%s path=%s",
                request_id,
                request.method,
                request.url.path,
            )
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request_end request_id=%s method=%s path=%s status=%s duration_ms=%.2f",
                request_id,
                request.method,
                request.url.path,
                response.status_code,
                (perf_counter() - started) * 1000,
            )
            return response
        finally:
            REQUEST_ID.reset(token)

    @staticmethod
    def _error(status: int, code: str, request_id: str) -> JSONResponse:
        message = (
            "The request body exceeds the configured limit."
            if status == 413
            else "The Content-Length header is invalid."
        )
        return JSONResponse(
            status_code=status,
            content={"error": {"code": code, "message": message, "request_id": request_id}},
            headers={"X-Request-ID": request_id},
        )


class SearchAdmissionController:
    """A process-local cap for expensive scans; intentionally not a distributed limiter."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active = 0
        self._lock = asyncio.Lock()
        self._deferred_releases: set[asyncio.Task[None]] = set()

    @asynccontextmanager
    async def admit(self) -> AsyncIterator["SearchLease"]:
        async with self._lock:
            if self._active >= self._limit:
                raise ScanCapacityError("Similarity-search capacity is currently full.")
            self._active += 1
        lease = SearchLease(self)
        try:
            yield lease
        finally:
            await lease.release()

    async def _release(self) -> None:
        async with self._lock:
            self._active -= 1

    def _defer_release(self, task: asyncio.Task[Any]) -> None:
        async def release_when_finished() -> None:
            try:
                await task
            except BaseException as error:
                logger.warning("detached_search_finished outcome=%s", type(error).__name__)
            finally:
                await self._release()

        release_task = asyncio.create_task(release_when_finished())
        self._deferred_releases.add(release_task)
        release_task.add_done_callback(self._deferred_releases.discard)


class SearchLease:
    def __init__(self, controller: SearchAdmissionController) -> None:
        self._controller = controller
        self._deferred_task: asyncio.Task[Any] | None = None

    def defer_release_until(self, task: asyncio.Task[Any]) -> None:
        self._deferred_task = task

    async def release(self) -> None:
        if self._deferred_task is not None:
            self._controller._defer_release(self._deferred_task)
        else:
            await self._controller._release()
