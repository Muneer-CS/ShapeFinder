from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from random import Random

import pytest

from shape_finder.application.similarity_engine import (
    ChartSimilarityEngine,
    InvalidSimilarityInputError,
)
from shape_finder.application.similarity_search import HistoricalSimilarityScanner
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.similarity import SimilarityScore
from shape_finder.core.similarity_search import SimilaritySearchQuery
from shape_finder.core.universe import UniverseKind, UniverseSelection

START = datetime(2020, 1, 1, tzinfo=UTC)
REFERENCE = [100, 104, 111, 107, 116, 123, 118, 128, 135, 132, 141, 149]
TOLERANCE = 1e-6


def assert_scores_close(left: SimilarityScore, right: SimilarityScore) -> None:
    for field in (
        "overall_score",
        "shape_score",
        "direction_score",
        "error_score",
        "amplitude_score",
    ):
        assert abs(getattr(left, field) - getattr(right, field)) <= TOLERANCE


def make_series(symbol: str, closes: Sequence[float | int | Decimal]) -> TimeSeries:
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


def query(symbols: Sequence[str], *, threshold: float | None = None) -> SimilaritySearchQuery:
    return SimilaritySearchQuery(
        reference_symbol="REF",
        reference_start=START,
        reference_end=START + timedelta(days=len(REFERENCE) - 1),
        interval=BarInterval.ONE_DAY,
        search_start=START,
        search_end=START + timedelta(days=199),
        candidate_symbols=tuple(symbols),
        universe=UniverseSelection(UniverseKind.US_EQUITIES),
        top_n=10,
        minimum_similarity=threshold,
    )


def test_batch_components_match_scalar_cases_and_irregular_lengths() -> None:
    engine = ChartSimilarityEngine()
    prepared = engine.prepare(REFERENCE)
    candidates: list[Sequence[float | int | Decimal]] = [
        REFERENCE,
        [value * 7 for value in REFERENCE],
        [100 + (value - 100) * 0.35 for value in REFERENCE],
        [value + (index % 3 - 1) * 1.7 for index, value in enumerate(REFERENCE)],
        [100, 108, 103, 115, 106, 121, 112, 130, 119, 138, 125, 143],
        [10_000 / value for value in REFERENCE],
        [100] * len(REFERENCE),
        [100 + index for index in range(len(REFERENCE))],
        [REFERENCE[index] for index in range(0, len(REFERENCE), 2)],
        [Decimal("1e1000") * Decimal(value) for value in REFERENCE],
    ]
    optimized = engine.compare_many(prepared, candidates)
    legacy = tuple(engine.compare_prepared(prepared, item) for item in candidates)
    assert len(optimized) == len(legacy)
    for actual, expected in zip(optimized, legacy, strict=True):
        assert_scores_close(actual, expected)


def test_deterministic_random_batch_matches_repeated_scalar_comparisons() -> None:
    random = Random(90210)
    engine = ChartSimilarityEngine()
    prepared = engine.prepare(REFERENCE)
    windows = [
        [
            50 + index * random.uniform(-0.3, 0.7) + random.uniform(-2, 2)
            for index in range(len(REFERENCE))
        ]
        for _ in range(500)
    ]
    optimized = engine.compare_many(prepared, windows)
    legacy = tuple(engine.compare_prepared(prepared, item) for item in windows)
    for actual, expected in zip(optimized, legacy, strict=True):
        assert_scores_close(actual, expected)


def test_flat_and_inverted_batch_semantics_are_unchanged() -> None:
    engine = ChartSimilarityEngine()
    flat = engine.prepare([100] * 12)
    flat_scores = engine.compare_many(flat, [[500] * 12, REFERENCE])
    assert flat_scores[0] == SimilarityScore(100, 100, 100, 100, 100)
    assert flat_scores[1] == SimilarityScore(0, 0, 0, 0, 0)
    inverted = engine.compare_many(
        engine.prepare(REFERENCE), [[10_000 / value for value in REFERENCE]]
    )[0]
    assert inverted.overall_score < 20


def test_mixed_boolean_batch_retains_scalar_validation() -> None:
    engine = ChartSimilarityEngine()
    candidate = [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, True]
    with pytest.raises(InvalidSimilarityInputError):
        engine.compare_many(engine.prepare(REFERENCE), [candidate])


@pytest.mark.parametrize("batch_size", [1, 7, 64, 4096])
def test_batch_boundaries_preserve_legacy_ranking_threshold_and_timestamps(
    batch_size: int,
) -> None:
    candidates = [
        make_series(
            symbol,
            [70 + index * 0.05 + ((index * 13 + number * 7) % 17) * 0.12 for index in range(100)],
        )
        for number, symbol in enumerate(["AAA", "BBB", "CCC", "DDD"])
    ]
    for number, candidate in enumerate(candidates):
        values = [bar.close for bar in candidate.bars]
        offset = 70 + number * 3
        values[offset : offset + len(REFERENCE)] = [
            Decimal(str(value * (2 + number * 0.01))) for value in REFERENCE
        ]
        candidates[number] = make_series(candidate.symbol, values)
    reference = make_series("REF", REFERENCE)
    request = query([item.symbol for item in candidates], threshold=80)
    legacy = HistoricalSimilarityScanner(ChartSimilarityEngine(), use_batch=False).scan(
        reference, candidates, request
    )
    optimized = HistoricalSimilarityScanner(ChartSimilarityEngine(), batch_size=batch_size).scan(
        reference, candidates, request
    )

    assert [(match.symbol, match.start, match.end) for match in optimized.matches] == [
        (match.symbol, match.start, match.end) for match in legacy.matches
    ]
    assert optimized.statistics == legacy.statistics
    for actual, expected in zip(optimized.matches, legacy.matches, strict=True):
        assert_scores_close(actual.score, expected.score)


def test_near_threshold_and_close_rankings_match_legacy() -> None:
    engine = ChartSimilarityEngine()
    first = [value + (index % 2) * 0.0001 for index, value in enumerate(REFERENCE)]
    second = [value + (index % 2) * 0.0002 for index, value in enumerate(REFERENCE)]
    threshold = engine.compare(REFERENCE, second).overall_score
    candidates = [make_series("ZZZ", first), make_series("AAA", second)]
    request = query(["ZZZ", "AAA"], threshold=threshold)
    legacy = HistoricalSimilarityScanner(ChartSimilarityEngine(), use_batch=False).scan(
        make_series("REF", REFERENCE), candidates, request
    )
    optimized = HistoricalSimilarityScanner(ChartSimilarityEngine(), batch_size=1).scan(
        make_series("REF", REFERENCE), candidates, request
    )
    assert [(item.symbol, item.start) for item in optimized.matches] == [
        (item.symbol, item.start) for item in legacy.matches
    ]


def test_larger_multi_symbol_scan_matches_legacy_top_results() -> None:
    symbols = [f"S{number:02d}" for number in range(20)]
    candidates = []
    for number, symbol in enumerate(symbols):
        values = [
            40 + index * 0.02 + ((index * 19 + number * 11) % 31) * 0.07 for index in range(500)
        ]
        if number < 4:
            offset = 350 + number * 25
            values[offset : offset + len(REFERENCE)] = [value * (number + 2) for value in REFERENCE]
        candidates.append(make_series(symbol, values))
    request = replace(query(symbols, threshold=85), search_end=START + timedelta(days=499))
    legacy = HistoricalSimilarityScanner(ChartSimilarityEngine(), use_batch=False).scan(
        make_series("REF", REFERENCE), candidates, request
    )
    optimized = HistoricalSimilarityScanner(ChartSimilarityEngine(), batch_size=37).scan(
        make_series("REF", REFERENCE), candidates, request
    )
    assert [(item.symbol, item.start) for item in optimized.matches] == [
        (item.symbol, item.start) for item in legacy.matches
    ]
    for actual, expected in zip(optimized.matches, legacy.matches, strict=True):
        assert_scores_close(actual.score, expected.score)
