"""Controlled cached-search stress sanity; uses synthetic data and no network."""

import argparse
import asyncio
import gc
import tempfile
import tracemalloc
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from time import perf_counter

from shape_finder.application.market_data_service import MarketDataService
from shape_finder.application.similarity_engine import ChartSimilarityEngine
from shape_finder.application.similarity_search import (
    HistoricalSimilarityScanner,
    SimilaritySearchService,
)
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.persistence import CoverageRange
from shape_finder.core.similarity_search import SimilaritySearchQuery
from shape_finder.infrastructure.persistence.sqlite_market_data import (
    SQLiteMarketDataRepository,
)


class OfflineProvider:
    async def get_historical_bars(
        self, symbol: str, start: datetime, end: datetime, interval: BarInterval
    ) -> TimeSeries:
        raise AssertionError("A fully cached stress run must not use the provider.")


def make_series(symbol: str, bars: int, start: datetime) -> TimeSeries:
    values = [
        80 + index * 0.03 + ((index * 17 + len(symbol) * 11) % 23) * 0.12 for index in range(bars)
    ]
    return TimeSeries(
        symbol=symbol,
        interval=BarInterval.ONE_DAY,
        timezone="UTC",
        bars=tuple(
            PriceBar(
                timestamp=start + timedelta(days=index),
                open=Decimal(str(value)),
                high=Decimal(str(value + 1)),
                low=Decimal(str(value - 1)),
                close=Decimal(str(value)),
                volume=Decimal("1000"),
            )
            for index, value in enumerate(values)
        ),
    )


async def run(args: argparse.Namespace) -> None:
    start = datetime(2020, 1, 1, tzinfo=UTC)
    output = Path("validation-output")
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output) as directory:
        repository = SQLiteMarketDataRepository(Path(directory) / "stress.sqlite3")
        await repository.initialize()
        symbols = [f"S{index:03d}" for index in range(args.symbols)]
        all_series = [make_series("REF", 30, start)] + [
            make_series(symbol, args.bars, start) for symbol in symbols
        ]
        coverages = [
            CoverageRange(item.bars[0].timestamp, item.bars[-1].timestamp, start)
            for item in all_series
        ]
        await repository.upsert_time_series(all_series, coverages, source="stress")
        service = SimilaritySearchService(
            MarketDataService(OfflineProvider(), repository),
            HistoricalSimilarityScanner(ChartSimilarityEngine()),
        )
        query = SimilaritySearchQuery(
            reference_symbol="REF",
            reference_start=start,
            reference_end=start + timedelta(days=29),
            interval=BarInterval.ONE_DAY,
            search_start=start,
            search_end=start + timedelta(days=args.bars - 1),
            candidate_symbols=tuple(symbols),
            top_n=10,
        )

        tracemalloc.start()
        started = perf_counter()
        sequential = [await service.search(query) for _ in range(args.repeats)]
        concurrent = await asyncio.gather(*(service.search(query) for _ in range(args.concurrent)))
        elapsed = perf_counter() - started
        gc.collect()
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        signatures = {
            tuple(
                (match.symbol, match.start, match.score.overall_score) for match in result.matches
            )
            for result in [*sequential, *concurrent]
        }
        if len(signatures) != 1:
            raise RuntimeError("Repeated cached searches were not deterministic.")
        print(f"searches={args.repeats + args.concurrent}")
        print(f"concurrent_searches={args.concurrent}")
        print(f"windows_per_search={sequential[0].statistics.windows_evaluated}")
        print(f"runtime_seconds={elapsed:.3f}")
        print(f"retained_memory_mib={current / 1024 / 1024:.2f}")
        print(f"peak_traced_memory_mib={peak / 1024 / 1024:.2f}")
        print("failures=0")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--concurrent", type=int, default=4)
    parser.add_argument("--symbols", type=int, default=8)
    parser.add_argument("--bars", type=int, default=500)
    args = parser.parse_args()
    if min(args.repeats, args.concurrent, args.symbols) < 1 or args.bars < 30:
        parser.error("counts must be positive and bars must be at least 30")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
