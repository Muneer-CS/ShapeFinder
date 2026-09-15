import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from shape_finder.api.safety import SearchAdmissionController, configure_logging
from shape_finder.config import Settings
from shape_finder.core.errors import ScanCapacityError
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.persistence import CoverageRange
from shape_finder.infrastructure.persistence.sqlite_market_data import (
    SQLiteMarketDataRepository,
)
from shape_finder.main import create_app


def settings(path: Path, **values: object) -> Settings:
    return Settings(_env_file=None, database_path=path, **values)  # type: ignore[call-arg]


def valid_series(symbol: str = "AAPL") -> TimeSeries:
    start = datetime(2025, 1, 2, tzinfo=UTC)
    return TimeSeries(
        symbol=symbol,
        interval=BarInterval.ONE_DAY,
        timezone="UTC",
        bars=tuple(
            PriceBar(
                timestamp=start + timedelta(days=index),
                open=Decimal(100 + index),
                high=Decimal(102 + index),
                low=Decimal(99 + index),
                close=Decimal(101 + index),
                volume=Decimal(1_000),
            )
            for index in range(3)
        ),
    )


def test_malformed_configuration_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="app_env"):
        settings(tmp_path / "bad.sqlite3", app_env="staging")
    with pytest.raises(ValidationError, match="explicit HTTP"):
        settings(tmp_path / "bad.sqlite3", cors_origins=["*"])
    with pytest.raises(ValidationError, match="Production requires"):
        settings(tmp_path / "bad.sqlite3", app_env="production", cors_origins=[])
    with pytest.raises(ValidationError, match="less than or equal to 65535"):
        settings(tmp_path / "bad.sqlite3", api_port=70_000)


def test_http_client_logging_cannot_emit_credential_query_urls() -> None:
    configure_logging("DEBUG")
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("httpcore").getEffectiveLevel() >= logging.WARNING


def test_cors_is_explicit_and_request_ids_are_returned(tmp_path: Path) -> None:
    application = create_app(
        settings=settings(tmp_path / "cors.sqlite3", cors_origins=["https://shape.example"])
    )
    with TestClient(application) as client:
        allowed = client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://shape.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        denied = client.options(
            "/api/v1/health",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        response = client.get("/api/v1/health", headers={"X-Request-ID": "request-1234"})
    assert allowed.headers["access-control-allow-origin"] == "https://shape.example"
    assert "access-control-allow-origin" not in denied.headers
    assert response.headers["x-request-id"] == "request-1234"


def test_safe_unexpected_error_never_exposes_secret(tmp_path: Path) -> None:
    application = create_app(settings=settings(tmp_path / "error.sqlite3"))

    async def explode() -> None:
        raise RuntimeError("provider failed with apikey=do-not-leak")

    application.add_api_route("/explode", explode)
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get("/explode")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert response.json()["error"]["request_id"] == response.headers["x-request-id"]
    assert "do-not-leak" not in response.text


def test_request_body_limit_is_enforced_before_routing(tmp_path: Path) -> None:
    application = create_app(
        settings=settings(tmp_path / "limit.sqlite3", max_request_body_bytes=1_024)
    )
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/similarity/search",
            content="x" * 1_025,
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_readiness_checks_local_database_only(tmp_path: Path) -> None:
    application = create_app(settings=settings(tmp_path / "ready.sqlite3"))
    with TestClient(application) as client:
        response = client.get("/api/v1/readiness")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "shape-finder-api",
        "version": "0.2.0-rc.1",
        "database": "ready",
    }


def test_readiness_failure_is_safe(tmp_path: Path) -> None:
    application = create_app(settings=settings(tmp_path / "not-ready.sqlite3"))

    class BrokenReadiness:
        async def check_readiness(self) -> None:
            raise sqlite3.DatabaseError("C:/secret/path.sqlite3 is corrupt")

    with TestClient(application) as client:
        application.state.repository = BrokenReadiness()
        response = client.get("/api/v1/readiness")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "NOT_READY"
    assert "secret" not in response.text


@pytest.mark.anyio
async def test_process_local_search_admission_limit() -> None:
    controller = SearchAdmissionController(1)
    async with controller.admit():
        with pytest.raises(ScanCapacityError):
            async with controller.admit():
                pytest.fail("second search should not be admitted")
    async with controller.admit():
        pass


@pytest.mark.anyio
async def test_admission_stays_held_until_timed_out_work_finishes() -> None:
    controller = SearchAdmissionController(1)
    task = asyncio.create_task(asyncio.sleep(0.02))
    async with controller.admit() as lease:
        lease.defer_release_until(task)
    with pytest.raises(ScanCapacityError):
        async with controller.admit():
            pytest.fail("detached work must retain its admission slot")
    await task
    await asyncio.sleep(0.05)
    async with controller.admit():
        pass


def test_search_timeout_returns_safe_504(tmp_path: Path) -> None:
    application = create_app(
        settings=settings(tmp_path / "timeout.sqlite3", scan_timeout_seconds=0.01)
    )

    class SlowSearch:
        async def search(self, _query: object) -> None:
            await asyncio.sleep(0.1)

    request = {
        "reference": {
            "symbol": "AAPL",
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-01-10T00:00:00Z",
            "interval": "1day",
        },
        "search": {
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-02-01T00:00:00Z",
            "symbols": ["MSFT"],
        },
    }
    with TestClient(application) as client:
        application.state.similarity_search_service = SlowSearch()
        response = client.post("/api/v1/similarity/search", json=request)
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "SEARCH_TIMEOUT"


@pytest.mark.anyio
async def test_concurrent_sqlite_reads_and_failed_batch_are_safe(tmp_path: Path) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "concurrent.sqlite3")
    await repository.initialize()
    series = valid_series()
    coverage = CoverageRange(
        start=series.bars[0].timestamp,
        end=series.bars[-1].timestamp,
        synced_at=datetime.now(UTC),
    )
    await repository.upsert_time_series([series], [coverage], source="test")
    reads = await asyncio.gather(
        *(
            repository.get_time_series("AAPL", BarInterval.ONE_DAY, coverage.start, coverage.end)
            for _ in range(20)
        )
    )
    assert all(item == series for item in reads)

    invalid = TimeSeries(
        symbol="MSFT",
        interval=BarInterval.ONE_DAY,
        timezone="UTC",
        bars=(
            PriceBar(
                timestamp=coverage.start,
                open=Decimal("NaN"),
                high=Decimal("2"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
            ),
        ),
    )
    with pytest.raises(ValueError, match="finite and positive"):
        await repository.upsert_time_series(
            [valid_series("NVDA"), invalid], [coverage, coverage], source="test"
        )
    untouched = await repository.get_time_series(
        "NVDA", BarInterval.ONE_DAY, coverage.start, coverage.end
    )
    assert untouched.bars == ()


@pytest.mark.anyio
async def test_unknown_migration_version_fails_startup(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at_utc TEXT)"
        )
        connection.execute("INSERT INTO schema_migrations VALUES (999, '2025-01-01T00:00:00Z')")
    with pytest.raises(RuntimeError, match="newer"):
        await SQLiteMarketDataRepository(path).initialize()
