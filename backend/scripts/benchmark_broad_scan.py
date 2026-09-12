"""Deterministic local broad-scanner benchmark; not a CI timing assertion."""

import argparse
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import perf_counter

from shape_finder.application.similarity_engine import ChartSimilarityEngine
from shape_finder.application.similarity_search import HistoricalSimilarityScanner
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.similarity_search import SimilaritySearchQuery
from shape_finder.core.universe import UniverseKind, UniverseSelection


def make_series(symbol: str, values: list[float], start: datetime) -> TimeSeries:
    return TimeSeries(
        symbol=symbol,
        interval=BarInterval.ONE_DAY,
        timezone="UTC",
        bars=tuple(
            PriceBar(
                timestamp=start + timedelta(days=index),
                open=Decimal(str(value)),
                high=Decimal(str(value)),
                low=Decimal(str(value)),
                close=Decimal(str(value)),
                volume=Decimal("1000"),
            )
            for index, value in enumerate(values)
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", type=int, default=100)
    parser.add_argument("--bars", type=int, default=1000)
    args = parser.parse_args()
    start = datetime(2020, 1, 1, tzinfo=UTC)
    reference_values = [100 + index * 0.25 + ((index * 7) % 11) * 0.7 for index in range(30)]
    reference = make_series("REF", reference_values, start)
    candidates: list[TimeSeries] = []
    symbols: list[str] = []
    for number in range(args.symbols):
        symbol = f"S{number:04d}"
        symbols.append(symbol)
        values = [
            60 + index * 0.01 + ((index * 17 + number * 19) % 29) * 0.08
            for index in range(args.bars)
        ]
        if number < 5:
            offset = args.bars - 100 - number * 35
            values[offset : offset + 30] = [value * (number + 2) for value in reference_values]
        candidates.append(make_series(symbol, values, start))
    query = SimilaritySearchQuery(
        reference_symbol="REF",
        reference_start=start,
        reference_end=start + timedelta(days=29),
        interval=BarInterval.ONE_DAY,
        search_start=start,
        search_end=start + timedelta(days=args.bars - 1),
        candidate_symbols=tuple(symbols),
        universe=UniverseSelection(UniverseKind.US_EQUITIES),
        top_n=10,
        minimum_similarity=85,
    )
    scanner = HistoricalSimilarityScanner(ChartSimilarityEngine())
    started = perf_counter()
    result = scanner.scan(reference, candidates, query)
    elapsed = perf_counter() - started
    windows = result.statistics.windows_evaluated
    print(f"symbols={args.symbols}")
    print(f"bars_per_symbol={args.bars}")
    print(f"windows_evaluated={windows}")
    print(f"runtime_seconds={elapsed:.3f}")
    print(f"comparisons_per_second={windows / elapsed:,.0f}")
    print(f"top_match={result.matches[0].symbol}:{result.matches[0].score.overall_score}")


if __name__ == "__main__":
    main()
