import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from shape_finder.application.market_data_service import MarketDataService
from shape_finder.application.similarity_engine import ChartSimilarityEngine
from shape_finder.application.similarity_search import (
    HistoricalSimilarityScanner,
    SimilaritySearchService,
)
from shape_finder.application.universe import UniverseService
from shape_finder.application.universe_hydration import UniverseHydrationService
from shape_finder.core.errors import (
    DailyQuotaError,
    NoDataError,
    ProviderNetworkError,
    ProviderRejectedError,
    RateLimitError,
)
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.persistence import CoverageRange
from shape_finder.core.similarity_search import SimilaritySearchQuery
from shape_finder.core.universe import SymbolMetadata, UniverseKind, UniverseSelection
from shape_finder.infrastructure.persistence.sqlite_market_data import SQLiteMarketDataRepository

START = datetime(2020, 1, 1, tzinfo=UTC)
END = START + timedelta(days=20)
NOW = datetime(2030, 1, 1, tzinfo=UTC)


def metadata(symbol: str, exchange: str = "NASDAQ") -> SymbolMetadata:
    return SymbolMetadata(
        symbol=symbol,
        name=f"{symbol} Corp",
        exchange=exchange,
        country="United States",
        security_type="Common Stock",
        currency="USD",
    )


def series(
    symbol: str,
    *,
    interval: BarInterval = BarInterval.ONE_DAY,
    count: int = 21,
    scale: int = 1,
) -> TimeSeries:
    step = timedelta(days=1) if interval is BarInterval.ONE_DAY else timedelta(minutes=5)
    return TimeSeries(
        symbol=symbol,
        interval=interval,
        timezone="UTC",
        bars=tuple(
            PriceBar(
                timestamp=START + step * index,
                open=Decimal(100 + index * scale),
                high=Decimal(100 + index * scale),
                low=Decimal(100 + index * scale),
                close=Decimal(100 + index * scale),
                volume=Decimal(1000),
            )
            for index in range(count)
        ),
    )


class HydrationProvider:
    def __init__(
        self,
        catalog: Sequence[SymbolMetadata],
        data: dict[str, TimeSeries | Exception],
        *,
        delay: float = 0,
    ) -> None:
        self.catalog = tuple(catalog)
        self.data = data
        self.delay = delay
        self.calls: list[tuple[str, datetime, datetime, BarInterval]] = []

    async def list_stocks(self) -> Sequence[SymbolMetadata]:
        return self.catalog

    async def get_historical_bars(
        self, symbol: str, start: datetime, end: datetime, interval: BarInterval
    ) -> TimeSeries:
        self.calls.append((symbol, start, end, interval))
        if self.delay:
            await asyncio.sleep(self.delay)
        value = self.data[symbol]
        if isinstance(value, Exception):
            raise value
        return value


async def services(
    tmp_path: Path,
    provider: HydrationProvider,
    *,
    max_symbols: int = 5,
    intraday_max_symbols: int = 2,
    timeout_seconds: float = 15,
) -> tuple[
    SQLiteMarketDataRepository,
    MarketDataService,
    UniverseService,
    UniverseHydrationService,
]:
    repository = SQLiteMarketDataRepository(tmp_path / "hydration.sqlite3")
    await repository.initialize()
    market = MarketDataService(provider, repository, clock=lambda: NOW)
    universe = UniverseService(provider, repository, clock=lambda: NOW)
    hydration = UniverseHydrationService(
        market,
        repository,
        universe,
        max_symbols=max_symbols,
        intraday_max_symbols=intraday_max_symbols,
        timeout_seconds=timeout_seconds,
        clock=lambda: NOW,
    )
    return repository, market, universe, hydration


@pytest.mark.anyio
async def test_zero_ready_hydrates_bounded_batch_then_progresses_without_refetch(
    tmp_path: Path,
) -> None:
    provider = HydrationProvider(
        [metadata(symbol) for symbol in ("AAA", "BBB", "CCC", "DDD")],
        {symbol: series(symbol) for symbol in ("AAA", "BBB", "CCC", "DDD")},
    )
    repository, _, _, hydration = await services(tmp_path, provider, max_symbols=2)

    first = await hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9)
    second = await hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9)

    assert first.hydration_limit == first.attempted == first.succeeded == 2
    assert len(first.ready_before) == 0
    assert len(first.ready_after) == 2
    assert second.ready_before == first.ready_after
    assert second.ready_after == ("AAA", "BBB", "CCC", "DDD")
    assert len(provider.calls) == 4
    assert {call[0] for call in provider.calls} == {"AAA", "BBB", "CCC", "DDD"}
    assert all(call[1:] == (START, END, BarInterval.ONE_DAY) for call in provider.calls)

    reopened = SQLiteMarketDataRepository(tmp_path / "hydration.sqlite3")
    await reopened.initialize()
    assert await reopened.get_scan_ready_symbols(
        ["AAA", "BBB", "CCC", "DDD"], BarInterval.ONE_DAY, START, END, 9
    ) == ("AAA", "BBB", "CCC", "DDD")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (UniverseKind.NASDAQ, "AAA"),
        (UniverseKind.NYSE, "IBM"),
        (UniverseKind.US_EQUITIES, "IBM"),
    ],
)
async def test_named_universe_selection_is_respected(
    tmp_path: Path, kind: UniverseKind, expected: str
) -> None:
    provider = HydrationProvider(
        [metadata("AAA"), metadata("IBM", "NYSE")],
        {"AAA": series("AAA"), "IBM": series("IBM")},
    )
    _, _, _, hydration = await services(tmp_path, provider, max_symbols=1)
    result = await hydration.hydrate(kind, START, END, BarInterval.ONE_DAY, 9)
    assert result.attempted == 1
    assert provider.calls[0][0] == expected


@pytest.mark.anyio
async def test_rate_limit_preserves_success_and_next_search_advances(tmp_path: Path) -> None:
    provider = HydrationProvider(
        [metadata(symbol) for symbol in ("AAA", "BBB", "CCC")],
        {
            "AAA": RateLimitError(),
            "BBB": series("BBB"),
            "CCC": series("CCC"),
        },
    )
    _, _, _, hydration = await services(tmp_path, provider, max_symbols=3)

    first = await hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9)
    second = await hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9)

    assert first.succeeded == 1
    assert first.failed == 1
    assert first.provider_rate_limited
    assert first.ready_after == ("CCC",)
    assert second.ready_after == ("BBB", "CCC")
    assert [call[0] for call in provider.calls] == ["CCC", "AAA", "BBB", "AAA"]


@pytest.mark.anyio
async def test_no_data_for_one_symbol_does_not_block_later_symbols(tmp_path: Path) -> None:
    provider = HydrationProvider(
        [metadata("AAA"), metadata("BBB")],
        {"AAA": NoDataError(), "BBB": series("BBB")},
    )
    _, _, _, hydration = await services(tmp_path, provider, max_symbols=2)
    result = await hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9)
    assert (result.attempted, result.succeeded, result.failed) == (2, 1, 1)
    assert result.ready_after == ("BBB",)
    assert result.failure_counts == {"no_data": 1}
    assert (result.fetched, result.persisted, result.became_ready) == (1, 1, 1)


@pytest.mark.anyio
async def test_permanent_failure_cooldown_skips_immediate_repeat_and_tries_next(
    tmp_path: Path,
) -> None:
    provider = HydrationProvider(
        [metadata("AAA"), metadata("BBB")],
        {"AAA": ProviderRejectedError("rejected", provider_code=400), "BBB": series("BBB")},
    )
    repository, _, _, hydration = await services(tmp_path, provider, max_symbols=1)

    first = await hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9)
    second = await hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9)

    assert first.failure_counts == {"provider_rejected": 1}
    assert await repository.get_suppressed_hydration_symbols(
        ("AAA",), BarInterval.ONE_DAY, START, END, NOW
    ) == ("AAA",)
    assert second.suppressed == 1
    assert second.became_ready == 1
    assert [call[0] for call in provider.calls] == ["AAA", "BBB"]


@pytest.mark.anyio
async def test_transient_failure_remains_retryable(tmp_path: Path) -> None:
    provider = HydrationProvider([metadata("AAA")], {"AAA": ProviderNetworkError()})
    _, _, _, hydration = await services(tmp_path, provider, max_symbols=1)
    first = await hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9)
    provider.data["AAA"] = series("AAA")
    second = await hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9)
    assert first.failure_counts == {"provider_unavailable": 1}
    assert second.became_ready == 1
    assert [call[0] for call in provider.calls] == ["AAA", "AAA"]


@pytest.mark.anyio
async def test_fetch_success_with_insufficient_bars_is_classified_separately(
    tmp_path: Path,
) -> None:
    provider = HydrationProvider([metadata("AAA")], {"AAA": series("AAA", count=5)})
    _, _, _, hydration = await services(tmp_path, provider)
    result = await hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9)
    assert (result.fetched, result.persisted, result.became_ready) == (1, 1, 0)
    assert result.failure_counts == {"insufficient_coverage": 1}
    assert result.ready_after == ()


@pytest.mark.anyio
async def test_daily_quota_stops_batch_with_distinct_status(tmp_path: Path) -> None:
    provider = HydrationProvider(
        [metadata("AAA"), metadata("BBB")],
        {"AAA": DailyQuotaError(), "BBB": series("BBB")},
    )
    _, _, _, hydration = await services(tmp_path, provider)
    result = await hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9)
    assert result.provider_daily_quota
    assert not result.provider_rate_limited
    assert result.failure_counts == {"provider_daily_quota": 1}
    assert [call[0] for call in provider.calls] == ["AAA"]


@pytest.mark.anyio
async def test_search_scans_newly_hydrated_symbols_and_reports_exact_metadata(
    tmp_path: Path,
) -> None:
    provider = HydrationProvider(
        [metadata("AAA"), metadata("BBB"), metadata("CCC")],
        {symbol: series(symbol) for symbol in ("AAA", "BBB", "CCC")},
    )
    repository, market, universe, hydration = await services(tmp_path, provider, max_symbols=2)
    reference = series("REF", count=9)
    await repository.upsert_time_series(
        [reference], [CoverageRange(START, START + timedelta(days=8), NOW)], source="test"
    )
    service = SimilaritySearchService(
        market,
        HistoricalSimilarityScanner(ChartSimilarityEngine()),
        repository,
        universe,
        hydration,
    )

    result = await service.search(
        SimilaritySearchQuery(
            "REF",
            START,
            START + timedelta(days=8),
            BarInterval.ONE_DAY,
            START,
            END,
            universe=UniverseSelection(UniverseKind.NASDAQ),
        )
    )

    statistics = result.statistics
    assert statistics.universe_symbols_total == statistics.symbols_requested == 3
    assert statistics.ready_before_hydration == 0
    assert statistics.hydration_limit == statistics.hydration_attempted == 2
    assert statistics.hydration_succeeded == 2
    assert statistics.hydration_failed == 0
    assert statistics.hydration_fetched == 2
    assert statistics.hydration_persisted == 2
    assert statistics.hydration_became_ready == 2
    assert statistics.hydration_failure_counts == {}
    assert statistics.ready_after_hydration == statistics.symbols_eligible == 2
    assert statistics.symbols_scanned == 2
    assert statistics.symbols_skipped == 1
    assert statistics.windows_evaluated > 0
    assert {match.symbol for match in result.matches} <= {"AAA", "CCC"}


@pytest.mark.anyio
async def test_provider_failure_still_scans_cached_stock_and_reports_metadata(
    tmp_path: Path,
) -> None:
    provider = HydrationProvider(
        [metadata("AAA"), metadata("READY")],
        {"AAA": ProviderNetworkError(), "READY": series("READY")},
    )
    repository, market, universe, hydration = await services(tmp_path, provider, max_symbols=2)
    reference = series("REF", count=9)
    ready = series("READY", scale=2)
    await repository.upsert_time_series(
        [reference, ready],
        [CoverageRange(START, START + timedelta(days=8), NOW), CoverageRange(START, END, NOW)],
        source="test",
    )
    service = SimilaritySearchService(
        market,
        HistoricalSimilarityScanner(ChartSimilarityEngine()),
        repository,
        universe,
        hydration,
    )
    result = await service.search(
        SimilaritySearchQuery(
            "REF",
            START,
            START + timedelta(days=8),
            BarInterval.ONE_DAY,
            START,
            END,
            universe=UniverseSelection(UniverseKind.NASDAQ),
        )
    )
    assert result.statistics.ready_before_hydration == 1
    assert result.statistics.ready_after_hydration == 1
    assert result.statistics.symbols_scanned == 1
    assert result.statistics.hydration_provider_unavailable


@pytest.mark.anyio
async def test_provider_failure_with_zero_ready_returns_clear_partial_result(
    tmp_path: Path,
) -> None:
    provider = HydrationProvider([metadata("AAA")], {"AAA": ProviderNetworkError()})
    repository, market, universe, hydration = await services(tmp_path, provider)
    reference = series("REF", count=9)
    await repository.upsert_time_series(
        [reference], [CoverageRange(START, START + timedelta(days=8), NOW)], source="test"
    )
    service = SimilaritySearchService(
        market,
        HistoricalSimilarityScanner(ChartSimilarityEngine()),
        repository,
        universe,
        hydration,
    )
    result = await service.search(
        SimilaritySearchQuery(
            "REF",
            START,
            START + timedelta(days=8),
            BarInterval.ONE_DAY,
            START,
            END,
            universe=UniverseSelection(UniverseKind.NASDAQ),
        )
    )
    assert result.matches == ()
    assert result.statistics.symbols_scanned == 0
    assert result.statistics.hydration_provider_unavailable


@pytest.mark.anyio
async def test_intraday_limit_and_requested_interval_are_conservative(tmp_path: Path) -> None:
    end = START + timedelta(minutes=100)
    provider = HydrationProvider(
        [metadata(symbol) for symbol in ("AAA", "BBB", "CCC")],
        {
            symbol: series(symbol, interval=BarInterval.FIVE_MINUTES)
            for symbol in ("AAA", "BBB", "CCC")
        },
    )
    _, _, _, hydration = await services(tmp_path, provider, max_symbols=5, intraday_max_symbols=2)
    result = await hydration.hydrate(UniverseKind.NASDAQ, START, end, BarInterval.FIVE_MINUTES, 9)
    assert result.hydration_limit == result.attempted == 2
    assert all(call[1:] == (START, end, BarInterval.FIVE_MINUTES) for call in provider.calls)


@pytest.mark.anyio
async def test_hydration_time_budget_preserves_completed_work(tmp_path: Path) -> None:
    provider = HydrationProvider(
        [metadata("AAA"), metadata("BBB")],
        {"AAA": series("AAA"), "BBB": series("BBB")},
        delay=0.03,
    )
    _, _, _, hydration = await services(tmp_path, provider, max_symbols=2, timeout_seconds=0.01)
    result = await hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9)
    assert result.timed_out
    assert result.attempted == result.failed == 1
    assert len(provider.calls) <= 1


@pytest.mark.anyio
async def test_concurrent_identical_hydration_does_not_duplicate_fetches(tmp_path: Path) -> None:
    provider = HydrationProvider(
        [metadata("AAA"), metadata("BBB")],
        {"AAA": series("AAA"), "BBB": series("BBB")},
        delay=0.01,
    )
    _, _, _, hydration = await services(tmp_path, provider, max_symbols=1)
    results = await asyncio.gather(
        hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9),
        hydration.hydrate(UniverseKind.NASDAQ, START, END, BarInterval.ONE_DAY, 9),
    )
    assert [call[0] for call in provider.calls] == ["AAA", "BBB"]
    assert [len(result.ready_after) for result in results] == [1, 2]
