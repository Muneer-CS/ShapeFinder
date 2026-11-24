from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shape_finder.application.massive_daily import load_cached_chart
from shape_finder.config import Settings
from shape_finder.core.errors import MissingApiKeyError, RateLimitError
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.infrastructure.persistence.current_data_store import CurrentDataStore
from shape_finder.infrastructure.persistence.massive_store import DailyBar, MassiveStore
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


@pytest.mark.parametrize(
    ("symbol", "start", "end", "gap", "status", "code", "detail"),
    [
        ("nvda", "2026-10-24", "2026-11-10", False, 200, "", ""),
        ("NVDA", "2026-10-23", "2026-11-10", False, 422, "OUT_OF_RANGE", "2026-10-26"),
        ("NVDA", "2026-10-24", "2026-11-11", False, 422, "OUT_OF_RANGE", "2026-11-10"),
        ("NVDA", "2026-10-24", "2026-11-10", True, 422, "GAP", "2026-11-03"),
        ("NVDA", "2026-10-24", "2026-10-25", False, 422, "CLOSED", "No trading sessions"),
        ("UNSAVED", "2026-10-24", "2026-11-10", False, 404, "EMPTY", "No saved daily candles"),
    ],
)
def test_massive_reference_chart(
    tmp_path: Path,
    symbol: str,
    start: str,
    end: str,
    gap: bool,
    status: int,
    code: str,
    detail: str,
) -> None:
    class UnexpectedProvider:
        async def get_historical_bars(
            self, symbol: str, start: datetime, end: datetime, interval: BarInterval
        ) -> TimeSeries:
            pytest.fail("Reference chart attempted a provider call.")

    dates = [
        "2026-10-26",
        "2026-10-27",
        "2026-10-28",
        "2026-10-29",
        "2026-10-30",
        "2026-11-02",
        "2026-11-03",
        "2026-11-04",
        "2026-11-05",
        "2026-11-06",
        "2026-11-09",
        "2026-11-10",
    ]
    settings = Settings(
        _env_file=None,
        app_env="test",
        current_daily_provider="massive",
        database_path=tmp_path / "massive.sqlite3",
    )
    store = MassiveStore(settings.database_path)
    retrieved = datetime(2026, 11, 20, tzinfo=UTC)
    with TestClient(create_app(UnexpectedProvider(), settings=settings)) as client:
        for position, day in enumerate(dates):
            if not gap or day != "2026-11-03":
                price = Decimal(100 + position)
                store.save_session(
                    date.fromisoformat(day),
                    [DailyBar("NVDA", price, price, price, price, Decimal(10))],
                    retrieved,
                    retrieved,
                )
        response = client.get(
            f"/api/v1/market-data/{symbol}",
            params={"start": f"{start}T00:00:00Z", "end": f"{end}T23:59:59Z", "interval": "1day"},
        )
    assert response.status_code == status, response.json()
    body = response.json()
    if status == 200:
        assert body["symbol"] == "NVDA"
        assert body["cached_range"] == {"start": dates[0], "end": dates[-1]}
        assert [bar["timestamp"][:10] for bar in body["bars"]] == dates
        assert body["bars"][0]["timestamp"] == "2026-10-26T13:30:00Z"
        assert body["bars"][-1]["timestamp"] == "2026-11-10T14:30:00Z"
    else:
        assert body["error"]["code"] == f"CACHED_CHART_{code}"
        assert detail in body["error"]["message"]


def test_massive_mode_keeps_intraday_provider(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        app_env="test",
        current_daily_provider="massive",
        database_path=tmp_path / "intraday.sqlite3",
    )
    with TestClient(create_app(StubProvider(RateLimitError()), settings=settings)) as client:
        response = client.get(
            "/api/v1/market-data/NVDA",
            params={
                "start": "2025-01-02T14:30:00Z",
                "end": "2025-01-02T15:30:00Z",
                "interval": "5min",
            },
        )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "PROVIDER_RATE_LIMITED"


def test_massive_reference_chart_skips_exchange_holiday(tmp_path: Path) -> None:
    db = tmp_path / "holiday.sqlite3"
    CurrentDataStore(db).initialize()
    store = MassiveStore(db)
    retrieved = datetime(2026, 11, 28, 12, tzinfo=UTC)
    price = Decimal(100)
    for day in (25, 27):
        store.save_session(
            date(2026, 11, day),
            [DailyBar("NVDA", price, price, price, price, Decimal(10))],
            retrieved,
            retrieved,
        )
    series, _, _ = load_cached_chart(
        store,
        "nvda",
        datetime(2026, 11, 25, tzinfo=UTC),
        datetime(2026, 11, 28, 23, tzinfo=UTC),
        time(0, 5),
    )
    assert [bar.timestamp.day for bar in series.bars] == [25, 27]
