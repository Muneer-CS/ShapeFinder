import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from shape_finder.application.market_data_service import MarketDataService
from shape_finder.application.similarity_engine import ChartSimilarityEngine
from shape_finder.application.similarity_search import (
    HistoricalSimilarityScanner,
    SimilaritySearchService,
)
from shape_finder.application.universe import UniverseService
from shape_finder.core.errors import (
    MalformedProviderResponseError,
    MissingApiKeyError,
    ProviderNetworkError,
    RateLimitError,
)
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.persistence import CoverageRange
from shape_finder.core.similarity_search import (
    InvalidSimilaritySearchError,
    SimilaritySearchQuery,
)
from shape_finder.core.universe import SymbolMetadata, UniverseKind, UniverseSelection
from shape_finder.infrastructure.market_data.twelve_data import TwelveDataProvider
from shape_finder.infrastructure.persistence.sqlite_market_data import SQLiteMarketDataRepository
from shape_finder.main import create_app

START = datetime(2020, 1, 1, tzinfo=UTC)
NOW = datetime(2026, 9, 11, tzinfo=UTC)
REFERENCE = [100, 104, 111, 106, 115, 121, 117, 126, 132]


def metadata(
    symbol: str,
    exchange: str = "NASDAQ",
    *,
    country: str = "United States",
    security_type: str = "Common Stock",
    active: bool = True,
) -> SymbolMetadata:
    return SymbolMetadata(
        symbol=symbol,
        name=f"{symbol} Incorporated",
        exchange=exchange,
        country=country,
        security_type=security_type,
        currency="USD",
        active=active,
    )


def series(symbol: str, closes: Sequence[float | int]) -> TimeSeries:
    return TimeSeries(
        symbol=symbol,
        interval=BarInterval.ONE_DAY,
        timezone="UTC",
        bars=tuple(
            PriceBar(
                timestamp=START + timedelta(days=index),
                open=Decimal(str(close)),
                high=Decimal(str(close)),
                low=Decimal(str(close)),
                close=Decimal(str(close)),
                volume=Decimal("1000"),
            )
            for index, close in enumerate(closes)
        ),
    )


class CatalogProvider:
    def __init__(
        self,
        catalog: Sequence[SymbolMetadata] = (),
        error: Exception | None = None,
    ) -> None:
        self.catalog = tuple(catalog)
        self.error = error
        self.catalog_calls = 0

    async def list_stocks(self) -> Sequence[SymbolMetadata]:
        self.catalog_calls += 1
        if self.error:
            raise self.error
        return self.catalog

    async def get_historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> TimeSeries:
        raise MissingApiKeyError()


@pytest.mark.anyio
async def test_concurrent_universe_reads_share_one_refresh(tmp_path: Path) -> None:
    class SlowCatalogProvider(CatalogProvider):
        async def list_stocks(self) -> Sequence[SymbolMetadata]:
            await asyncio.sleep(0.01)
            return await super().list_stocks()

    repository = SQLiteMarketDataRepository(tmp_path / "universe-lock.sqlite3")
    await repository.initialize()
    provider = SlowCatalogProvider([metadata("AAPL")])
    service = UniverseService(provider, repository, clock=lambda: NOW)

    results = await asyncio.gather(*(service.list_universes() for _ in range(8)))

    assert all(result[0].total_symbols == 1 for result in results)
    assert provider.catalog_calls == 1


@pytest.mark.anyio
async def test_universe_normalizes_filters_deduplicates_and_selects_exchange(
    tmp_path: Path,
) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "universe.sqlite3")
    await repository.initialize()
    provider = CatalogProvider(
        [
            metadata(" aapl ", "nasdaq"),
            metadata("AAPL", "OTC"),
            metadata("IBM", "NYSE"),
            metadata("SPY", security_type="ETF"),
            metadata("RY", country="Canada"),
            metadata("OLD", active=False),
            metadata("!OTC/FLZH", "NASDAQ"),
        ]
    )
    service = UniverseService(provider, repository, clock=lambda: NOW)

    descriptors = await service.list_universes()
    nasdaq, _, stale = await service.resolve(UniverseKind.NASDAQ)
    nyse, _, _ = await service.resolve(UniverseKind.NYSE)

    assert [(item.id, item.total_symbols) for item in descriptors] == [
        (UniverseKind.US_EQUITIES, 2),
        (UniverseKind.NASDAQ, 1),
        (UniverseKind.NYSE, 1),
    ]
    assert nasdaq == ("AAPL",)
    assert nyse == ("IBM",)
    assert not stale
    assert provider.catalog_calls == 1


@pytest.mark.anyio
async def test_universe_ttl_stale_fallback_and_atomic_replacement(tmp_path: Path) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "refresh.sqlite3")
    await repository.initialize()
    fresh_provider = CatalogProvider([metadata("AAPL"), metadata("IBM", "NYSE")])
    initial = UniverseService(fresh_provider, repository, clock=lambda: NOW)
    await initial.list_universes()
    assert fresh_provider.catalog_calls == 1

    cached = UniverseService(
        CatalogProvider(error=MissingApiKeyError()),
        repository,
        clock=lambda: NOW + timedelta(hours=12),
    )
    assert not (await cached.list_universes())[0].stale

    failing_provider = CatalogProvider(error=ProviderNetworkError())
    stale = UniverseService(
        failing_provider,
        repository,
        clock=lambda: NOW + timedelta(days=2),
    )
    assert (await stale.list_universes())[0].stale
    assert failing_provider.catalog_calls == 1

    replacement = UniverseService(
        CatalogProvider([metadata("MSFT")]),
        repository,
        clock=lambda: NOW + timedelta(days=3),
    )
    assert (await replacement.resolve(UniverseKind.US_EQUITIES))[0] == ("MSFT",)
    assert [item.symbol for item in await repository.list_universe_symbols()] == ["MSFT"]


@pytest.mark.anyio
async def test_empty_universe_without_provider_is_an_error(tmp_path: Path) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "empty.sqlite3")
    await repository.initialize()
    service = UniverseService(
        CatalogProvider(error=MissingApiKeyError()), repository, clock=lambda: NOW
    )
    with pytest.raises(MissingApiKeyError):
        await service.list_universes()


@pytest.mark.anyio
async def test_scan_readiness_enforces_coverage_interval_period_and_bar_count(
    tmp_path: Path,
) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "ready.sqlite3")
    await repository.initialize()
    full = series("FULL", range(20, 40))
    short = series("SHORT", range(10, 14))
    end = START + timedelta(days=19)
    await repository.upsert_time_series(
        [full, short],
        [CoverageRange(START, end, NOW), CoverageRange(START, end, NOW)],
        source="test",
    )

    assert await repository.get_scan_ready_symbols(
        ["FULL", "SHORT"], BarInterval.ONE_DAY, START, end, 9
    ) == ("FULL",)
    assert not await repository.get_scan_ready_symbols(
        ["FULL"], BarInterval.FIVE_MINUTES, START, end, 9
    )
    assert not await repository.get_scan_ready_symbols(
        ["FULL"], BarInterval.ONE_DAY, START - timedelta(days=1), end, 9
    )


@pytest.mark.anyio
async def test_broad_search_scans_only_ready_cache_and_reports_truthful_coverage(
    tmp_path: Path,
) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "broad.sqlite3")
    await repository.initialize()
    candidate = series("AMD", [70, 72, *[value * 2 for value in REFERENCE]])
    short = series("MISS", [10, 11, 12])
    reference = series("NVDA", REFERENCE)
    end = START + timedelta(days=10)
    await repository.upsert_time_series(
        [reference, candidate, short],
        [
            CoverageRange(START, START + timedelta(days=8), NOW),
            CoverageRange(START, end, NOW),
            CoverageRange(START, end, NOW),
        ],
        source="test",
    )
    provider = CatalogProvider([metadata("AMD"), metadata("MISS")])
    universe = UniverseService(provider, repository, clock=lambda: NOW)
    market_data = MarketDataService(provider, repository, clock=lambda: NOW + timedelta(days=365))
    service = SimilaritySearchService(
        market_data,
        HistoricalSimilarityScanner(ChartSimilarityEngine()),
        repository,
        universe,
    )
    query = SimilaritySearchQuery(
        reference_symbol="NVDA",
        reference_start=START,
        reference_end=START + timedelta(days=8),
        interval=BarInterval.ONE_DAY,
        search_start=START,
        search_end=end,
        universe=UniverseSelection(UniverseKind.US_EQUITIES),
        top_n=1,
        minimum_similarity=90,
    )

    first = await service.search(query)
    second = await service.search(query)
    assert first == second
    assert first.matches[0].symbol == "AMD"
    assert first.matches[0].score.overall_score == 100
    assert first.statistics.universe_symbols_total == 2
    assert first.statistics.symbols_eligible == first.statistics.symbols_scanned == 1
    assert first.statistics.symbols_skipped == 1
    assert first.statistics.windows_evaluated == 3


@pytest.mark.anyio
async def test_broad_search_with_no_ready_symbols_returns_clear_zero_coverage(
    tmp_path: Path,
) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "none-ready.sqlite3")
    await repository.initialize()
    reference = series("NVDA", REFERENCE)
    await repository.upsert_time_series(
        [reference],
        [CoverageRange(START, START + timedelta(days=8), NOW)],
        source="test",
    )
    provider = CatalogProvider([metadata("AMD")])
    universe = UniverseService(provider, repository, clock=lambda: NOW)
    service = SimilaritySearchService(
        MarketDataService(provider, repository, clock=lambda: NOW + timedelta(days=365)),
        HistoricalSimilarityScanner(ChartSimilarityEngine()),
        repository,
        universe,
    )
    result = await service.search(
        SimilaritySearchQuery(
            "NVDA",
            START,
            START + timedelta(days=8),
            BarInterval.ONE_DAY,
            START,
            START + timedelta(days=30),
            universe=UniverseSelection(UniverseKind.NASDAQ),
        )
    )
    assert result.matches == ()
    assert result.statistics.symbols_eligible == 0
    assert result.statistics.symbols_skipped == 1


@pytest.mark.anyio
async def test_large_100_symbol_broad_scan_is_deterministic_and_finds_embedded_patterns(
    tmp_path: Path,
) -> None:
    repository = SQLiteMarketDataRepository(tmp_path / "large-broad.sqlite3")
    await repository.initialize()
    reference_values = [100 + index * 0.35 + ((index * 7) % 9) * 0.8 for index in range(30)]
    reference = series("REF", reference_values)
    candidates: list[TimeSeries] = []
    catalog: list[SymbolMetadata] = []
    complete_end = START + timedelta(days=499)
    coverage: list[CoverageRange] = [CoverageRange(START, START + timedelta(days=29), NOW)]
    for number in range(100):
        symbol = f"S{number:03d}"
        catalog.append(metadata(symbol, "NASDAQ" if number % 2 == 0 else "NYSE"))
        length = 10 if number >= 90 else 500
        values = [
            50 + index * 0.015 + ((index * 17 + number * 13) % 23) * 0.09 for index in range(length)
        ]
        if number < 5:
            offset = 300 + number * 10
            values[offset : offset + 30] = [value * (number + 2) for value in reference_values]
        candidates.append(series(symbol, values))
        coverage.append(CoverageRange(START, complete_end, NOW))
    await repository.upsert_time_series([reference, *candidates], coverage, source="synthetic")
    provider = CatalogProvider(catalog)
    universe = UniverseService(provider, repository, clock=lambda: NOW)
    service = SimilaritySearchService(
        MarketDataService(provider, repository, clock=lambda: NOW + timedelta(days=365)),
        HistoricalSimilarityScanner(ChartSimilarityEngine()),
        repository,
        universe,
    )
    search_query = SimilaritySearchQuery(
        reference_symbol="REF",
        reference_start=START,
        reference_end=START + timedelta(days=29),
        interval=BarInterval.ONE_DAY,
        search_start=START,
        search_end=complete_end,
        universe=UniverseSelection(UniverseKind.US_EQUITIES),
        top_n=5,
        minimum_similarity=99,
    )

    first = await service.search(search_query)
    assert {match.symbol for match in first.matches} == {
        "S000",
        "S001",
        "S002",
        "S003",
        "S004",
    }
    assert all(match.score.overall_score == 100 for match in first.matches)
    assert first.statistics.universe_symbols_total == 100
    assert first.statistics.symbols_eligible == first.statistics.symbols_scanned == 90
    assert first.statistics.symbols_skipped == 10
    assert first.statistics.windows_evaluated == 90 * 471


@pytest.mark.anyio
async def test_broad_search_window_safety_limit_stops_before_loading_candidates() -> None:
    class LimitRepository:
        loaded = False

        async def get_scan_ready_symbols(
            self,
            symbols: Sequence[str],
            interval: BarInterval,
            start: datetime,
            end: datetime,
            minimum_bars: int,
        ) -> Sequence[str]:
            return symbols

        async def get_time_series(
            self,
            symbol: str,
            interval: BarInterval,
            start: datetime,
            end: datetime,
        ) -> TimeSeries:
            self.loaded = True
            raise AssertionError("Safety validation must happen before candidate loading")

    class LimitUniverse:
        async def resolve(
            self, kind: UniverseKind
        ) -> tuple[tuple[str, ...], datetime | None, bool]:
            return tuple(f"S{index:04d}" for index in range(5000)), NOW, False

    repository = LimitRepository()

    class ReferenceMarket:
        async def get_time_series(self, *args: object) -> TimeSeries:
            return series("REF", REFERENCE)

    service = SimilaritySearchService(
        ReferenceMarket(),  # type: ignore[arg-type]
        HistoricalSimilarityScanner(ChartSimilarityEngine()),
        repository,  # type: ignore[arg-type]
        LimitUniverse(),  # type: ignore[arg-type]
    )
    with pytest.raises(InvalidSimilaritySearchError, match="window safety limit"):
        await service.search(
            SimilaritySearchQuery(
                "REF",
                START,
                START + timedelta(days=8),
                BarInterval.ONE_DAY,
                START,
                START + timedelta(days=730),
                universe=UniverseSelection(UniverseKind.US_EQUITIES),
            )
        )
    assert not repository.loaded


@pytest.mark.anyio
async def test_twelve_data_stock_catalog_parser_and_error_mapping() -> None:
    success = {
        "data": [
            {
                "symbol": "aapl",
                "name": "Apple Inc",
                "exchange": "NASDAQ",
                "country": "United States",
                "type": "Common Stock",
                "currency": "usd",
                "active": True,
            }
        ]
    }

    async def fetch(payload: Any, status: int = 200) -> tuple[SymbolMetadata, ...]:
        transport = httpx.MockTransport(lambda _: httpx.Response(status, json=payload))
        async with httpx.AsyncClient(
            transport=transport, base_url="https://api.twelvedata.com"
        ) as client:
            return await TwelveDataProvider(client, "key", retry_attempts=1).list_stocks()

    assert (await fetch(success))[0].symbol == "AAPL"
    with_incomplete_row = {"data": success["data"] * 100 + [{**success["data"][0], "name": ""}]}
    assert len(await fetch(with_incomplete_row)) == 100
    with pytest.raises(MalformedProviderResponseError):
        await fetch({"data": [success["data"][0], {**success["data"][0], "name": ""}]})
    with pytest.raises(MalformedProviderResponseError):
        await fetch({"data": [{"symbol": "AAPL"}]})
    with pytest.raises(MalformedProviderResponseError):
        await fetch({"unexpected": []})
    with pytest.raises(RateLimitError):
        await fetch({"code": 429, "status": "error", "message": "limit"}, 429)


def test_universe_api_and_backward_compatible_custom_search(tmp_path: Path) -> None:
    provider = CatalogProvider([metadata("AMD")])
    repository = SQLiteMarketDataRepository(tmp_path / "api.sqlite3")
    body = {
        "reference": {
            "symbol": "NVDA",
            "start": "2020-01-01T00:00:00Z",
            "end": "2020-01-09T00:00:00Z",
            "interval": "1day",
        },
        "search": {
            "start": "2020-01-01T00:00:00Z",
            "end": "2020-01-11T00:00:00Z",
            "universe": {"kind": "us_equities"},
        },
        "top_n": 1,
    }
    with TestClient(create_app(provider, repository)) as client:
        universes = client.get("/api/v1/universes")
        broad = client.post("/api/v1/similarity/search", json=body)
        invalid = client.post(
            "/api/v1/similarity/search",
            json={
                **body,
                "search": {
                    **body["search"],
                    "symbols": ["AMD"],
                },
            },
        )
    assert universes.status_code == 200
    assert universes.json()["universes"][0]["id"] == "us_equities"
    assert broad.status_code == 503  # reference history is intentionally absent
    assert invalid.status_code == 422
