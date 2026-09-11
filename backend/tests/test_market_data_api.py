from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from shape_finder.core.errors import MissingApiKeyError, RateLimitError
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.infrastructure.persistence.sqlite_market_data import SQLiteMarketDataRepository
from shape_finder.main import create_app


class StubProvider:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def get_historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> TimeSeries:
        if self.error:
            raise self.error
        return TimeSeries(
            symbol=symbol,
            interval=interval,
            timezone="UTC",
            bars=(
                PriceBar(
                    timestamp=datetime(2025, 1, 2, 15, 30, tzinfo=UTC),
                    open=Decimal("100.1"),
                    high=Decimal("102.2"),
                    low=Decimal("99.9"),
                    close=Decimal("101.5"),
                    volume=Decimal("12345"),
                ),
            ),
        )


def test_market_data_api_returns_normalized_response(tmp_path: Path) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "api.sqlite3")
    with TestClient(create_app(StubProvider(), repository)) as client:
        response = client.get(
            "/api/v1/market-data/aapl",
            params={
                "start": "2025-01-01T00:00:00Z",
                "end": "2025-01-03T00:00:00Z",
                "interval": "1day",
            },
        )

    assert response.status_code == 200
    assert response.json() == {
        "symbol": "AAPL",
        "interval": "1day",
        "timezone": "UTC",
        "bars": [
            {
                "timestamp": "2025-01-02T15:30:00Z",
                "open": "100.1",
                "high": "102.2",
                "low": "99.9",
                "close": "101.5",
                "volume": "12345",
            }
        ],
    }


def test_market_data_api_rejects_invalid_range(tmp_path: Path) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "api.sqlite3")
    with TestClient(create_app(StubProvider(), repository)) as client:
        response = client.get(
            "/api/v1/market-data/AAPL",
            params={
                "start": "2025-01-03T00:00:00Z",
                "end": "2025-01-01T00:00:00Z",
                "interval": "1day",
            },
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DATE_RANGE"


def test_market_data_api_validates_interval_and_timezone(tmp_path: Path) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "api.sqlite3")
    with TestClient(create_app(StubProvider(), repository)) as client:
        response = client.get(
            "/api/v1/market-data/AAPL",
            params={
                "start": "2025-01-01T00:00:00",
                "end": "2025-01-03T00:00:00",
                "interval": "2min",
            },
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    with TestClient(create_app(StubProvider(), repository)) as client:
        response = client.get(
            "/api/v1/market-data/AAPL",
            params={
                "start": "2025-01-01T00:00:00",
                "end": "2025-01-03T00:00:00",
                "interval": "1day",
            },
        )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DATE_RANGE"


def test_market_data_api_maps_provider_errors(tmp_path: Path) -> None:
    cases = [
        (MissingApiKeyError(), 503, "PROVIDER_NOT_CONFIGURED"),
        (RateLimitError(), 429, "PROVIDER_RATE_LIMITED"),
    ]
    for index, (error, expected_status, expected_code) in enumerate(cases):
        repository = SQLiteMarketDataRepository(tmp_path / f"api-{index}.sqlite3")
        with TestClient(create_app(StubProvider(error), repository)) as client:
            response = client.get(
                "/api/v1/market-data/AAPL",
                params={
                    "start": "2025-01-01T00:00:00Z",
                    "end": "2025-01-03T00:00:00Z",
                    "interval": "1day",
                },
            )
        assert response.status_code == expected_status
        assert response.json()["error"]["code"] == expected_code
