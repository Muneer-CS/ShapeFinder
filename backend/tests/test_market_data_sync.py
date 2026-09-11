from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from shape_finder.application.market_data_service import MarketDataService
from shape_finder.core.errors import MissingApiKeyError, ProviderNetworkError
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.persistence import CoverageRange
from shape_finder.infrastructure.persistence.sqlite_market_data import SQLiteMarketDataRepository

NOW = datetime(2025, 6, 1, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeProvider:
    def __init__(self, error: Exception | None = None, close: str = "100") -> None:
        self.calls: list[tuple[datetime, datetime]] = []
        self.error = error
        self.close = Decimal(close)

    async def get_historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> TimeSeries:
        self.calls.append((start, end))
        if self.error:
            raise self.error
        step = timedelta(days=1) if interval is BarInterval.ONE_DAY else timedelta(hours=1)
        timestamp = start
        bars: list[PriceBar] = []
        while timestamp <= end:
            bars.append(
                PriceBar(
                    timestamp=timestamp,
                    open=self.close,
                    high=self.close,
                    low=self.close,
                    close=self.close,
                    volume=Decimal("1000"),
                )
            )
            timestamp += step
        return TimeSeries(symbol, interval, "UTC", tuple(bars))


async def repository(tmp_path: Path) -> SQLiteMarketDataRepository:
    result = SQLiteMarketDataRepository(tmp_path / "sync.sqlite3")
    await result.initialize()
    return result


@pytest.mark.anyio
async def test_empty_cache_persists_then_historical_request_is_cache_hit(tmp_path: Path) -> None:
    store = await repository(tmp_path)
    provider = FakeProvider()
    service = MarketDataService(provider, store, clock=lambda: NOW)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 3, tzinfo=UTC)

    first = await service.get_time_series("AAPL", start, end, BarInterval.ONE_DAY)
    second = await service.get_time_series("AAPL", start, end, BarInterval.ONE_DAY)

    assert len(first.bars) == len(second.bars) == 3
    assert provider.calls == [(start, end)]


@pytest.mark.anyio
async def test_partial_cache_fetches_only_missing_tail(tmp_path: Path) -> None:
    store = await repository(tmp_path)
    seed_provider = FakeProvider()
    seed = await seed_provider.get_historical_bars(
        "AAPL",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 2, 29, tzinfo=UTC),
        BarInterval.ONE_DAY,
    )
    await store.upsert_time_series(
        [seed],
        [CoverageRange(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 29, tzinfo=UTC), NOW)],
        source="seed",
    )
    provider = FakeProvider()
    service = MarketDataService(provider, store, clock=lambda: NOW)

    await service.get_time_series(
        "AAPL",
        datetime(2024, 1, 1, tzinfo=UTC),
        datetime(2024, 3, 31, tzinfo=UTC),
        BarInterval.ONE_DAY,
    )

    assert provider.calls == [(datetime(2024, 3, 1, tzinfo=UTC), datetime(2024, 3, 31, tzinfo=UTC))]


@pytest.mark.anyio
async def test_recent_edge_is_refreshed_but_historical_range_is_not(tmp_path: Path) -> None:
    store = await repository(tmp_path)
    start = datetime(2025, 5, 20, tzinfo=UTC)
    end = NOW
    seed_provider = FakeProvider(close="90")
    seed = await seed_provider.get_historical_bars("AAPL", start, end, BarInterval.ONE_DAY)
    await store.upsert_time_series(
        [seed], [CoverageRange(start, end, NOW - timedelta(hours=1))], source="seed"
    )

    provider = FakeProvider(close="110")
    service = MarketDataService(provider, store, clock=lambda: NOW)
    result = await service.get_time_series("AAPL", start, end, BarInterval.ONE_DAY)

    assert provider.calls == [(NOW - timedelta(days=3), NOW)]
    assert result.bars[-1].close == Decimal("110")

    provider.calls.clear()
    await service.get_time_series("AAPL", start, NOW - timedelta(days=4), BarInterval.ONE_DAY)
    assert provider.calls == []


@pytest.mark.anyio
async def test_no_key_is_irrelevant_for_complete_cache_but_required_for_gap(tmp_path: Path) -> None:
    store = await repository(tmp_path)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 3, tzinfo=UTC)
    seed_provider = FakeProvider()
    seed = await seed_provider.get_historical_bars("AAPL", start, end, BarInterval.ONE_DAY)
    await store.upsert_time_series([seed], [CoverageRange(start, end, NOW)], source="seed")
    unavailable = FakeProvider(MissingApiKeyError())
    service = MarketDataService(unavailable, store, clock=lambda: NOW)

    cached = await service.get_time_series("AAPL", start, end, BarInterval.ONE_DAY)
    assert len(cached.bars) == 3
    assert unavailable.calls == []

    with pytest.raises(MissingApiKeyError):
        await service.get_time_series("AAPL", start, end + timedelta(days=1), BarInterval.ONE_DAY)


@pytest.mark.anyio
async def test_no_key_returns_complete_recent_cache_without_refresh(tmp_path: Path) -> None:
    store = await repository(tmp_path)
    start = NOW - timedelta(days=2)
    seed_provider = FakeProvider()
    seed = await seed_provider.get_historical_bars("AAPL", start, NOW, BarInterval.ONE_DAY)
    await store.upsert_time_series([seed], [CoverageRange(start, NOW, NOW)], source="seed")
    unavailable = FakeProvider(MissingApiKeyError())
    service = MarketDataService(unavailable, store, clock=lambda: NOW)

    result = await service.get_time_series("AAPL", start, NOW, BarInterval.ONE_DAY)

    assert len(result.bars) == 3


@pytest.mark.anyio
async def test_provider_failure_preserves_existing_cache(tmp_path: Path) -> None:
    store = await repository(tmp_path)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    cached_end = datetime(2024, 1, 2, tzinfo=UTC)
    seed_provider = FakeProvider()
    seed = await seed_provider.get_historical_bars("AAPL", start, cached_end, BarInterval.ONE_DAY)
    await store.upsert_time_series([seed], [CoverageRange(start, cached_end, NOW)], source="seed")
    service = MarketDataService(FakeProvider(ProviderNetworkError()), store, clock=lambda: NOW)

    with pytest.raises(ProviderNetworkError):
        await service.get_time_series(
            "AAPL", start, datetime(2024, 1, 4, tzinfo=UTC), BarInterval.ONE_DAY
        )
    cached = await store.get_time_series("AAPL", BarInterval.ONE_DAY, start, cached_end)
    assert len(cached.bars) == 2


@pytest.mark.anyio
async def test_large_range_chunking_has_no_gaps_or_duplicates(tmp_path: Path) -> None:
    store = await repository(tmp_path)
    provider = FakeProvider()
    service = MarketDataService(provider, store, clock=lambda: NOW, max_points_per_chunk=3)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 8, tzinfo=UTC)

    result = await service.get_time_series("AAPL", start, end, BarInterval.ONE_DAY)

    assert provider.calls == [
        (datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 1, 3, tzinfo=UTC)),
        (datetime(2024, 1, 4, tzinfo=UTC), datetime(2024, 1, 6, tzinfo=UTC)),
        (datetime(2024, 1, 7, tzinfo=UTC), datetime(2024, 1, 8, tzinfo=UTC)),
    ]
    assert [item.timestamp.day for item in result.bars] == list(range(1, 9))
