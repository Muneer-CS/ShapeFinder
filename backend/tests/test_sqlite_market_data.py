import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.persistence import CoverageRange
from shape_finder.infrastructure.persistence.sqlite_market_data import SQLiteMarketDataRepository


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def bar(day: int, close: str, *, hour: int = 0) -> PriceBar:
    value = Decimal(close)
    return PriceBar(
        timestamp=datetime(2024, 1, day, hour, tzinfo=UTC),
        open=value - 1,
        high=value + 1,
        low=value - 2,
        close=value,
        volume=Decimal("1000.25"),
    )


def series(
    symbol: str = "AAPL",
    interval: BarInterval = BarInterval.ONE_DAY,
    bars: tuple[PriceBar, ...] = (bar(2, "101.1234"), bar(1, "100.1234")),
) -> TimeSeries:
    return TimeSeries(symbol=symbol, interval=interval, timezone="UTC", bars=bars)


def coverage(start_day: int = 1, end_day: int = 2) -> CoverageRange:
    return CoverageRange(
        start=datetime(2024, 1, start_day, tzinfo=UTC),
        end=datetime(2024, 1, end_day, tzinfo=UTC),
        synced_at=datetime(2024, 2, 1, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_initializes_versioned_schema_and_primary_key_index(tmp_path: Path) -> None:
    path = tmp_path / "market.sqlite3"
    repository = SQLiteMarketDataRepository(path)
    await repository.initialize()
    await repository.initialize()

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_schema WHERE type = 'table'")
        }
        version = connection.execute("SELECT version FROM schema_migrations").fetchone()
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM market_bars "
            "WHERE symbol = ? AND interval = ? AND timestamp_utc BETWEEN ? AND ?",
            ("AAPL", "1day", "2024", "2025"),
        ).fetchall()

    assert {"schema_migrations", "market_bars", "market_data_coverage"} <= tables
    assert version == (1,)
    assert any("PRIMARY KEY" in str(row) for row in plan)


@pytest.mark.anyio
async def test_batch_upsert_is_precise_chronological_and_idempotent(tmp_path: Path) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "market.sqlite3")
    await repository.initialize()
    await repository.upsert_time_series([series()], [coverage()], source="test")
    await repository.upsert_time_series([series()], [coverage()], source="test")

    result = await repository.get_time_series(
        "AAPL",
        BarInterval.ONE_DAY,
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
    )

    assert [item.timestamp.day for item in result.bars] == [1, 2]
    assert [item.close for item in result.bars] == [Decimal("100.1234"), Decimal("101.1234")]
    with sqlite3.connect(tmp_path / "market.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM market_bars").fetchone() == (2,)


@pytest.mark.anyio
async def test_upsert_updates_revised_bar(tmp_path: Path) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "market.sqlite3")
    await repository.initialize()
    await repository.upsert_time_series(
        [series(bars=(bar(1, "100"),))], [coverage(1, 1)], source="test"
    )
    await repository.upsert_time_series(
        [series(bars=(bar(1, "105.75"),))], [coverage(1, 1)], source="test"
    )

    result = await repository.get_time_series(
        "AAPL",
        BarInterval.ONE_DAY,
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 1, 1, tzinfo=UTC),
    )
    assert result.bars[0].close == Decimal("105.75")


@pytest.mark.anyio
async def test_queries_separate_symbol_interval_and_date_range(tmp_path: Path) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "market.sqlite3")
    await repository.initialize()
    items = [
        series("AAPL", BarInterval.ONE_DAY, (bar(1, "100"), bar(2, "101"))),
        series("MSFT", BarInterval.ONE_DAY, (bar(1, "200"),)),
        series("AAPL", BarInterval.ONE_HOUR, (bar(1, "300", hour=1),)),
    ]
    ranges = [coverage(), coverage(1, 1), coverage(1, 1)]
    await repository.upsert_time_series(items, ranges, source="test")

    result = await repository.get_time_series(
        "AAPL",
        BarInterval.ONE_DAY,
        datetime(2024, 1, 2, tzinfo=UTC),
        datetime(2024, 1, 2, tzinfo=UTC),
    )
    assert len(result.bars) == 1
    assert result.bars[0].close == Decimal("101")


@pytest.mark.anyio
async def test_rejects_misaligned_batch_without_writes(tmp_path: Path) -> None:
    path = tmp_path / "market.sqlite3"
    repository = SQLiteMarketDataRepository(path)
    await repository.initialize()
    with pytest.raises(ValueError):
        await repository.upsert_time_series([series()], [], source="test")
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM market_bars").fetchone() == (0,)
