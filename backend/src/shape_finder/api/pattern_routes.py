import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from shape_finder.api.current_scan_schemas import (
    CurrentPatternScanRequest,
    CurrentPatternScanResponse,
    PatternCapabilities,
    PatternScanError,
    PatternScanErrorResponse,
)
from shape_finder.api.schemas import ErrorResponse
from shape_finder.application.manual_pattern_scan import (
    ManualPatternScanService,
    PatternScanUnavailable,
    ScanCancellation,
)

router = APIRouter(tags=["current patterns"])


def _service(request: Request) -> ManualPatternScanService:
    service: ManualPatternScanService | None = request.app.state.current_pattern_service
    if service is None:
        raise PatternScanUnavailable("current_storage_unavailable")
    return service


def _error(
    request: Request, error: PatternScanUnavailable, body: CurrentPatternScanRequest | None = None
) -> JSONResponse:
    envelope = PatternScanErrorResponse(
        error=PatternScanError(
            reason=error.reason,
            request_id=str(getattr(request.state, "request_id", "unavailable")),
            details=error.details,
            revisions=body.revisions if body else None,
            pattern_content_hash=body.pattern.content_hash() if body else None,
        )
    )
    return JSONResponse(status_code=error.status, content=envelope.model_dump(mode="json"))


@router.get(
    "/pattern-scans/capabilities",
    response_model=PatternCapabilities,
    responses={503: {"model": PatternScanErrorResponse}},
)
async def capabilities(request: Request) -> PatternCapabilities | JSONResponse:
    try:
        return _service(request).capabilities()
    except PatternScanUnavailable as error:
        return _error(request, error)


@router.post(
    "/pattern-scans",
    response_model=CurrentPatternScanResponse,
    responses={
        **{code: {"model": PatternScanErrorResponse} for code in (499, 503, 504)},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
    },
)
async def pattern_scan(
    body: CurrentPatternScanRequest, request: Request
) -> CurrentPatternScanResponse | JSONResponse:
    started = time.monotonic()
    try:
        service = _service(request)
        service.validate_capability(
            body
        )  # Before admission, catalogue access, or provider attempts.
        cancellation = ScanCancellation()
        finished = False

        async def disconnected() -> None:
            while not finished:
                client_disconnected = await request.is_disconnected()
                # The receive poll's cancellation scope can consume watcher.cancel().
                if finished:
                    return
                if client_disconnected:
                    cancellation.stop()
                    return
                await asyncio.sleep(0.05)

        async with request.app.state.search_admission.admit() as lease:
            work = asyncio.create_task(service.scan(body, cancellation, started))
            watcher = asyncio.create_task(disconnected())
            try:
                return await asyncio.shield(work)
            except asyncio.CancelledError:
                cancellation.stop()
                lease.defer_release_until(work)
                raise
            finally:
                finished = True
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
    except PatternScanUnavailable as error:
        return _error(request, error, body)
