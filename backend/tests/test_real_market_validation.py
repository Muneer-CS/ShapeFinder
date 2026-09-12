from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from shape_finder.application.real_market_validation import (
    RealMarketValidationConfig,
    inspect_time_series,
    run_real_market_validation,
    validation_report_to_dict,
)
from shape_finder.application.similarity_engine import ChartSimilarityEngine
from shape_finder.application.similarity_search import HistoricalSimilarityScanner
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.similarity_search import SimilaritySearchQuery

START = datetime(2024, 1, 1, tzinfo=UTC)


def bar(
    day: int,
    close: str,
    *,
    opened: str | None = None,
    high: str | None = None,
    low: str | None = None,
    volume: str = "1000",
    tz: timezone | None = UTC,
) -> PriceBar:
    price = Decimal(close)
    return PriceBar(
        timestamp=datetime(2024, 1, day, tzinfo=tz),
        open=Decimal(opened) if opened else price,
        high=Decimal(high) if high else price,
        low=Decimal(low) if low else price,
        close=price,
        volume=Decimal(volume),
    )


def series(symbol: str, closes: list[int], *, start: datetime = START) -> TimeSeries:
    return TimeSeries(
        symbol,
        BarInterval.ONE_DAY,
        "UTC",
        tuple(
            PriceBar(
                timestamp=start + timedelta(days=index),
                open=Decimal(close),
                high=Decimal(close),
                low=Decimal(close),
                close=Decimal(close),
                volume=Decimal("1000"),
            )
            for index, close in enumerate(closes)
        ),
    )


def test_data_quality_diagnostics_report_corruption_without_rejecting_gaps() -> None:
    bad = TimeSeries(
        "BAD",
        BarInterval.ONE_DAY,
        "UTC",
        (
            bar(1, "100"),
            bar(1, "100"),
            bar(8, "105", opened="100", high="101", low="99", volume="0"),
            bar(7, "-1", volume="-1"),
            bar(9, "100", tz=None),
        ),
    )

    report = inspect_time_series(bad)
    issues = {issue.code: issue for issue in report.issues}

    assert report.error_count == 6
    assert issues["duplicate_timestamp"].count == 1
    assert issues["large_time_gap"].severity == "info"
    assert issues["malformed_ohlc"].count == 1
    assert issues["zero_volume"].severity == "warning"
    assert issues["out_of_order_timestamp"].count == 1
    assert issues["timezone_naive_timestamp"].count == 1
    assert issues["non_positive_or_non_finite_price"].count == 1
    assert issues["invalid_volume"].count == 1


def test_timezone_offset_inconsistency_is_reported() -> None:
    inconsistent = TimeSeries(
        "OFFSET",
        BarInterval.ONE_DAY,
        "UTC",
        (bar(1, "100", tz=timezone(timedelta(hours=-5))),),
    )
    assert [issue.code for issue in inspect_time_series(inconsistent).issues] == [
        "timezone_inconsistency"
    ]


class FixtureLoader:
    def __init__(self, values: dict[str, TimeSeries]) -> None:
        self.values = values
        self.calls: list[tuple[str, datetime, datetime, BarInterval]] = []

    async def get_time_series(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> TimeSeries:
        self.calls.append((symbol, start, end, interval))
        return self.values[symbol]


@pytest.mark.anyio
async def test_validation_runner_uses_normal_ranges_and_serializes_components() -> None:
    pattern = [100, 106, 102, 111, 108, 118]
    loader = FixtureLoader(
        {
            "REF": series("REF", pattern),
            "REF-HISTORY": series("REF-HISTORY", [80, 81, *pattern, 70, 69, *pattern]),
            "OTHER": series("OTHER", [value * 3 for value in pattern]),
        }
    )
    config = RealMarketValidationConfig(
        reference_symbol="REF",
        reference_start=START,
        reference_end=START + timedelta(days=5),
        interval=BarInterval.ONE_DAY,
        search_start=START,
        search_end=START + timedelta(days=15),
        candidate_symbols=("REF-HISTORY", "OTHER"),
        top_n=3,
    )

    report = await run_real_market_validation(
        loader, HistoricalSimilarityScanner(ChartSimilarityEngine()), config
    )
    payload = validation_report_to_dict(report)

    assert len(loader.calls) == 3
    assert loader.calls[0][1:3] == (config.reference_start, config.reference_end)
    assert all(call[1:3] == (config.search_start, config.search_end) for call in loader.calls[1:])
    assert payload["reference"]["bar_count"] == 6
    assert payload["matches"][0]["scores"]["overall_score"] == 100
    assert payload["statistics"]["windows_evaluated"] == 12
    assert payload["quality"][0]["error_count"] == 0
    assert payload["timing_seconds"]["total"] >= payload["timing_seconds"]["scan"]


@pytest.mark.parametrize("length", [6, 25, 80])
def test_short_medium_and_long_realistic_reference_lengths(length: int) -> None:
    closes = [100 + index + (index % 5) * 2 for index in range(length)]
    reference = series("SAME", closes)
    candidate = series("SAME", [*closes, 50, 49, *closes], start=START - timedelta(days=length + 2))
    config = RealMarketValidationConfig(
        reference_symbol="SAME",
        reference_start=reference.bars[0].timestamp,
        reference_end=reference.bars[-1].timestamp,
        interval=BarInterval.ONE_DAY,
        search_start=candidate.bars[0].timestamp,
        search_end=candidate.bars[-1].timestamp,
        candidate_symbols=("SAME",),
        top_n=2,
    )
    query = SimilaritySearchQuery(
        reference_symbol=config.reference_symbol,
        reference_start=config.reference_start,
        reference_end=config.reference_end,
        interval=config.interval,
        search_start=config.search_start,
        search_end=config.search_end,
        candidate_symbols=config.candidate_symbols,
        top_n=config.top_n,
    )

    result = HistoricalSimilarityScanner(ChartSimilarityEngine()).scan(
        reference, [candidate], query
    )

    assert result.matches
    assert result.matches[0].bar_count == length
    assert all(match.end <= config.search_end for match in result.matches)
