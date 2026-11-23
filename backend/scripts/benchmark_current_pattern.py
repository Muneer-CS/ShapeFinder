"""Synthetic offline workload; no providers, real calendars, or network access.

Run: python -m scripts.benchmark_current_pattern --stocks 10
"""

import argparse
import math
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from shape_finder.application.current_pattern_scan import CurrentPatternScanner, EligibleHistory
from shape_finder.core.candle_eligibility import CandleIdentity, Freshness, FrozenCutoff
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.pattern_scan import CanonicalPattern, CurrentScanSettings


def synthetic_history(symbol: str, closes: list[float]) -> EligibleHistory:
    """Every supplied day is an explicit fictional session, not a US calendar."""
    start = datetime(2025, 1, 1, 14, 30, tzinfo=UTC)
    required = tuple(
        CandleIdentity(
            interval=BarInterval.ONE_DAY,
            calendar_id="synthetic",
            session_date=(start + timedelta(days=i)).date(),
            timezone="UTC",
            open_at=start + timedelta(days=i),
            close_at=start + timedelta(days=i, hours=6),
        )
        for i in range(len(closes))
    )
    expected = required[-1]
    cutoff = expected.close_at + timedelta(minutes=3)
    fresh = Freshness(
        cutoff_at=cutoff,
        status="current",
        expected=expected,
        retrieved_at=cutoff,
        is_complete=True,
        publishable_at=expected.close_at + timedelta(minutes=1),
        finalizable_at=expected.close_at + timedelta(minutes=2),
        calendar_version="synthetic-1",
    )
    bars = tuple(
        PriceBar(
            timestamp=candle.open_at,
            open=Decimal(str(close)),
            high=Decimal(str(close)),
            low=Decimal(str(close)),
            close=Decimal(str(close)),
            volume=Decimal(1000),
        )
        for candle, close in zip(required, closes, strict=True)
    )
    return EligibleHistory(
        symbol, TimeSeries(symbol, BarInterval.ONE_DAY, "UTC", bars), fresh, required
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stocks", type=int, default=10)
    count = parser.parse_args().stocks
    if not 1 <= count <= 100:
        parser.error("stocks must be between 1 and 100")
    histories = [
        synthetic_history(
            f"S{i:03d}",
            [100 * math.exp(0.13 * math.sin(j / 7 + i / 10) + j / 2000) for j in range(120)],
        )
        for i in range(count)
    ]
    pattern = CanonicalPattern.model_validate(
        {
            "points": [
                {"x": 0, "y": 0},
                {"x": 0.25, "y": 1},
                {"x": 0.6, "y": -0.5},
                {"x": 1, "y": 0.7},
            ],
            "provenance": {"kind": "drawing"},
        }
    )
    settings = CurrentScanSettings()
    started = time.perf_counter()
    result = CurrentPatternScanner().scan(
        pattern, settings, FrozenCutoff(cutoff_at=histories[0].freshness.cutoff_at), histories
    )
    elapsed = time.perf_counter() - started
    print(
        f"stocks={count} bars_per_stock=120 duration=20..120 minimum_completion=0.5 "
        f"movement_percent=5 similarity=80 hypotheses={result.execution.hypotheses_evaluated} "
        f"scanned={result.coverage.scanned} elapsed_seconds={elapsed:.6f}"
    )


if __name__ == "__main__":
    main()
