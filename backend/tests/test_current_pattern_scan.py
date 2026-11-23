import math
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction

import numpy as np
import pytest

from scripts.benchmark_current_pattern import synthetic_history
from shape_finder.application.current_pattern_scan import (
    CurrentPatternScanner,
    ScanControl,
    hypotheses,
)
from shape_finder.application.current_shape import (
    clipped_prefix,
    log_close_path,
    prepare_candidate,
    prepare_prefix,
    score_pairs,
)
from shape_finder.application.similarity_engine import ChartSimilarityEngine
from shape_finder.core.candle_eligibility import Freshness, FrozenCutoff
from shape_finder.core.market_data import BarInterval
from shape_finder.core.pattern_scan import (
    CanonicalPattern,
    ComparisonObservation,
    CurrentScanSettings,
    MatchComparison,
)


def pattern(points: list[tuple[float, float]] | None = None) -> CanonicalPattern:
    return CanonicalPattern.model_validate(
        {
            "points": [{"x": x, "y": y} for x, y in (points or [(0, 0), (0.5, 1), (1, 0)])],
            "provenance": {"kind": "drawing"},
        }
    )


def settings(d: int = 21, **overrides: object) -> CurrentScanSettings:
    return CurrentScanSettings.model_validate(
        {
            "duration": {"minimum_candles": d, "maximum_candles": d},
            "minimum_similarity": 0,
            "minimum_movement_percent": 0,
            **overrides,
        }
    )


def prices(points: list[tuple[float, float]], n: int) -> list[float]:
    xs, ys = zip(*points, strict=True)
    return [100 * math.exp(float(y) * 0.1) for y in np.interp(np.linspace(0, 1, n), xs, ys)]


def scan(
    closes: list[float],
    p: CanonicalPattern | None = None,
    s: CurrentScanSettings | None = None,
    **limits: int,
):
    history = synthetic_history("AAA", closes)
    return CurrentPatternScanner(**limits).scan(
        p or pattern(),
        s or settings(),
        FrozenCutoff(cutoff_at=history.freshness.cutoff_at),
        [history],
    )


def test_clipping_inserts_hand_checkable_endpoint() -> None:
    p = pattern([(0, 2), (0.4, 6), (1, 0)])
    assert clipped_prefix(p, Fraction(1, 2)) == ((0, 2), (0.4, 6), (0.5, 5))
    with pytest.raises(ValueError):
        clipped_prefix(p, Fraction(0))


def test_comparison_contains_exact_scored_suffix_and_stable_log_path() -> None:
    values = prices([(0, 0), (1, 1)], 11)
    history = synthetic_history("AAA", values)
    result = scan(values, s=settings(match_type="forming", minimum_similarity=99))
    match = result.matches[0]
    snapshot = match.comparison
    assert snapshot is not None and match.observed_candles == 11
    assert snapshot.log_relative_closes == log_close_path([b.close for b in history.series.bars])
    assert [o.close for o in snapshot.observations] == [str(b.close) for b in history.series.bars]
    assert snapshot.observations[0].timestamp == match.start_at
    assert snapshot.observations[-1].timestamp == match.freshness.expected.open_at
    assert len(snapshot.observations) <= 240
    with pytest.raises(ValueError, match="exact matched suffix"):
        type(match)(
            **match.model_dump(exclude={"comparison", "estimated_completion", "match_type"}),
            comparison=MatchComparison(
                observations=snapshot.observations[:-1],
                log_relative_closes=snapshot.log_relative_closes[:-1],
            ),
        )


def test_comparison_contract_rejects_invalid_values_and_lengths() -> None:
    snapshot = scan(prices([(0, 0), (1, 1)], 21)).matches[0].comparison
    assert snapshot is not None
    payload = snapshot.model_dump()
    for change in ("negative", "not_decimal", "nonfinite", "unordered", "length", "origin"):
        data = snapshot.model_dump()
        if change == "negative":
            data["observations"][0]["close"] = "-1"
        elif change == "not_decimal":
            data["observations"][0]["close"] = "bad"
        elif change == "nonfinite":
            data["log_relative_closes"] = (0.0, float("inf"), *snapshot.log_relative_closes[2:])
        elif change == "unordered":
            data["observations"][1]["timestamp"] = data["observations"][0]["timestamp"]
        elif change == "length":
            data["log_relative_closes"] = snapshot.log_relative_closes[:-1]
        else:
            data["log_relative_closes"] = (1.0, *snapshot.log_relative_closes[1:])
        with pytest.raises(ValueError):
            MatchComparison.model_validate(data)
    assert MatchComparison.model_validate(payload) == snapshot


def test_comparison_ohlcv_is_optional_and_validates_complete_values() -> None:
    base = {"timestamp": "2026-11-20T14:30:00Z", "close": "102.00"}
    candle = {**base, "open": "100.50", "high": "106", "low": "99", "volume": "0"}
    assert ComparisonObservation.model_validate(base).volume is None
    assert ComparisonObservation.model_validate(candle).model_dump(mode="json") == candle
    with pytest.raises(ValueError, match="all candle values"):
        ComparisonObservation.model_validate({**candle, "open": None})
    with pytest.raises(ValueError, match="high and low must bound"):
        ComparisonObservation.model_validate({**candle, "high": "101"})
    with pytest.raises(ValueError, match="non-negative"):
        ComparisonObservation.model_validate({**candle, "volume": "-1"})


def test_large_price_encoding_omits_only_comparison_without_changing_rank() -> None:
    history = synthetic_history("AAA", [100.0 + i for i in range(21)])
    original = CurrentPatternScanner().scan(
        pattern(), settings(), FrozenCutoff(cutoff_at=history.freshness.cutoff_at), [history]
    )
    series = replace(
        history.series,
        bars=tuple(
            replace(b, close=Decimal(str(b.close) + "0" * 150)) for b in history.series.bars
        ),
    )
    result = CurrentPatternScanner().scan(
        pattern(),
        settings(),
        FrozenCutoff(cutoff_at=history.freshness.cutoff_at),
        [replace(history, series=series)],
    )
    assert result.matches[0].comparison is None
    assert result.matches[0].similarity_score == original.matches[0].similarity_score
    assert result.matches[0].observed_candles == original.matches[0].observed_candles
    assert result.execution == original.execution and result.coverage == original.coverage


def test_exact_enumeration_and_minimum_observations() -> None:
    hs = list(hypotheses(settings(), 21))
    assert [(h.observed, h.completion) for h in hs][:2] == [
        (11, Fraction(1, 2)),
        (12, Fraction(11, 20)),
    ]
    assert hs[-1].observed == 21 and len(hs) == 11
    assert not list(hypotheses(settings(), 10))
    assert next(hypotheses(settings(20), 20)).completion == Fraction(10, 19)
    assert len(list(hypotheses(CurrentScanSettings(), 120))) == 3560
    assert all(h.observed >= 8 for h in hypotheses(settings(8, minimum_completion=0.1), 8))


def test_full_and_forming_share_required_latest_end() -> None:
    p = pattern()
    values = prices([(0, 0), (0.5, 1), (1, 0)], 21)
    full = scan(values, p, settings(match_type="full", minimum_similarity=99.9))
    assert len(full.matches) == 1
    assert full.matches[0].estimated_completion == 1
    assert full.matches[0].similarity_score == pytest.approx(100, abs=1e-8)
    forming = scan(
        prices([(0, 0), (1, 1)], 11), p, settings(match_type="forming", minimum_similarity=99.9)
    )
    assert len(forming.matches) == 1
    match = forming.matches[0]
    assert match.observed_candles == 11 and match.estimated_completion == 0.5
    assert match.match_type == "forming"
    below = scan(prices([(0, 0), (1, 1)], 10), p, settings())
    assert not below.matches and below.exclusions[0].reason == "insufficient_history"


def test_superior_old_match_is_not_an_alternative_end() -> None:
    old = prices([(0, 0), (0.5, 1), (1, 0)], 21)
    latest = prices([(0, 0), (0.5, -1), (1, 0)], 21)
    values = old + [90, 91, 92] + latest
    r = scan(values, s=settings(match_type="full", minimum_similarity=99))
    assert not r.matches
    assert r.coverage.scanned == 1 and r.execution.hypotheses_evaluated == 1
    unfiltered = scan(values, s=settings(match_type="full"))
    assert (
        unfiltered.matches[0].start_at
        == synthetic_history("AAA", values).series.bars[-21].timestamp
    )
    assert unfiltered.matches[0].similarity_score < 50


def test_prefix_not_arbitrary_middle() -> None:
    p = pattern([(0, 0), (0.25, 1), (0.5, -1), (0.75, 0.5), (1, 0.6)])
    prefix = prices([(0, 0), (0.5, 1), (1, -1)], 21)
    middle = prices([(0, 1), (0.5, -1), (1, 0.5)], 21)
    s = settings(41, match_type="forming", minimum_similarity=95)
    accepted = scan(prefix, p, s)
    assert accepted.matches[0].estimated_completion == 0.5
    assert accepted.matches[0].similarity_score > 99.9
    assert not scan(middle, p, s).matches


@pytest.mark.parametrize("n", [21, 41, 81])
def test_faster_slower_full_shape(n: int) -> None:
    s = settings(
        duration={"minimum_candles": 20, "maximum_candles": 90},
        match_type="full",
        minimum_similarity=99.9,
    )
    r = scan(prices([(0, 0), (0.5, 1), (1, 0)], n), s=s)
    assert len(r.matches) == 1
    assert r.matches[0].complete_duration_candles == n
    assert r.matches[0].similarity_score > 99.9


def test_filter_before_best_alignment() -> None:
    values = prices([(0, 1), (1, -1)], 20) + prices([(0, 0), (1, 1)], 21)
    all_result = scan(values, s=settings(41))
    assert all_result.matches[0].match_type == "forming"
    full = scan(values, s=settings(41, match_type="full"))
    forming = scan(values, s=settings(41, match_type="forming"))
    assert full.matches[0].match_type == "full"
    assert forming.matches[0].match_type == "forming"
    assert full.matches[0].similarity_score < forming.matches[0].similarity_score


def test_large_v_movement_and_inclusive_threshold() -> None:
    values = prices([(0, 0), (0.5, -1), (1, 0)], 21)
    values[10] = 90
    p = pattern([(0, 0), (0.5, -1), (1, 0)])
    r = scan(values, p, settings(match_type="full", minimum_movement_percent=5))
    assert r.matches[0].movement_percent == pytest.approx(100 / 9)
    # Exactly 10 percent, endpoints equal, independent of drawing height.
    values = [100.0] * 21
    values[10] = 110
    assert scan(values, s=settings(match_type="full", minimum_movement_percent=10)).matches
    assert not scan(
        values, s=settings(match_type="full", minimum_movement_percent=10.00001)
    ).matches


def test_flat_candidates_and_flat_prefixes_never_score() -> None:
    r = scan([100.0] * 21)
    assert r.coverage.scanned == 1 and not r.matches
    assert r.execution.hypotheses_evaluated == 0
    p = pattern([(0, 0), (0.5, 0), (1, 1)])
    assert prepare_prefix(p, Fraction(1, 2)) is None
    r = scan(prices([(0, 0), (1, 1)], 11), p, settings(match_type="forming"))
    assert r.execution.hypotheses_evaluated == 0 and not r.matches


@pytest.mark.parametrize("scale,offset", [(1e-12, 0), (0.1, 200), (100, -200)])
def test_reference_scale_translation_invariance(scale: float, offset: float) -> None:
    points = [(0, 0), (0.3, 1), (0.7, -0.5), (1, 0.2)]
    values = prices([(0, 0), (0.4, 1), (0.8, -0.4), (1, 0.3)], 21)
    s = settings(match_type="full")
    base = scan(values, pattern(points), s).matches[0]
    changed = scan(values, pattern([(x, offset + scale * y) for x, y in points]), s).matches[0]
    assert changed.similarity_score == pytest.approx(base.similarity_score, abs=1e-8)


def test_continuation_does_not_leak_into_prefix_score() -> None:
    first = pattern([(0, 0), (0.25, 1), (0.5, -0.4), (1, 1)])
    second = pattern([(0, 0), (0.25, 1), (0.5, -0.4), (0.6, -999), (1, 1000)])
    values = prices([(0, 0), (0.5, 0.9), (1, -0.5)], 21)
    s = settings(41, match_type="forming")
    a, b = scan(values, first, s).matches[0], scan(values, second, s).matches[0]
    assert a.estimated_completion == b.estimated_completion == 0.5
    assert a.similarity_score == b.similarity_score


def test_component_math_agrees_with_legacy_for_normalized_pseudoprices() -> None:
    ref = prepare_prefix(pattern(), Fraction(1))
    candidate = prepare_candidate(
        log_close_path([Decimal(str(v)) for v in prices([(0, 0), (0.3, 1), (1, -0.2)], 21)])
    )
    assert ref is not None and candidate is not None
    current = score_pairs([(ref, candidate)])[0]
    legacy = ChartSimilarityEngine().compare(
        np.exp(ref.shape).tolist(), np.exp(candidate.shape).tolist()
    )
    assert current.shape == pytest.approx(legacy.shape_score, abs=1e-6)
    assert current.direction == pytest.approx(legacy.direction_score, abs=1e-6)
    assert current.error == pytest.approx(legacy.error_score, abs=1e-6)
    assert current.overall == pytest.approx(
        (0.45 * current.shape + 0.3 * current.direction + 0.2 * current.error) / 0.95
    )


def test_unrounded_threshold_applied_before_selection() -> None:
    values = prices([(0, 0), (0.4, 0.9), (1, -0.2)], 21)
    base = scan(values, s=settings(match_type="full")).matches[0].similarity_score
    assert scan(values, s=settings(match_type="full", minimum_similarity=base)).matches
    assert not scan(
        values, s=settings(match_type="full", minimum_similarity=math.nextafter(base, 100))
    ).matches


def test_ties_stock_order_and_limit_are_deterministic() -> None:
    # Every rising suffix has the same shape. Completion wins within rounded ties.
    p = pattern([(0, 0), (1, 1)])
    values = prices([(0, 0), (1, 1)], 41)
    histories = [synthetic_history(s, values) for s in ["ZZZ", "AAA", "MMM"]]
    s = settings(duration={"minimum_candles": 20, "maximum_candles": 41}, top_n=2)
    cutoff = FrozenCutoff(cutoff_at=histories[0].freshness.cutoff_at)
    for inputs in (histories, list(reversed(histories))):
        r = CurrentPatternScanner().scan(p, s, cutoff, inputs)
        assert [m.symbol for m in r.matches] == ["AAA", "MMM"]
        assert all(m.observed_candles == m.complete_duration_candles == 41 for m in r.matches)
        assert r.coverage.scanned == 3


@pytest.mark.parametrize(
    "failure",
    [
        "stale",
        "end",
        "unordered",
        "duplicate",
        "nan",
        "zero",
        "missing",
        "cutoff",
        "interval",
        "unknown",
        "unavailable",
    ],
)
def test_rejects_bad_histories(failure: str) -> None:
    h = synthetic_history("AAA", prices([(0, 0), (1, 1)], 21))
    cutoff = FrozenCutoff(cutoff_at=h.freshness.cutoff_at)
    assert h.series is not None
    bars = list(h.series.bars)
    if failure in ("stale", "unknown"):
        h = replace(
            h,
            freshness=Freshness(
                cutoff_at=cutoff.cutoff_at,
                status="excluded",
                reason="stale_bar" if failure == "stale" else "unknown_capability",
            ),
        )
    elif failure == "end":
        bars = bars[:-1]
    elif failure == "unordered":
        bars[0], bars[1] = bars[1], bars[0]
    elif failure == "duplicate":
        bars[1] = bars[0]
    elif failure in ("nan", "zero"):
        bars[0] = replace(bars[0], close=Decimal("NaN" if failure == "nan" else "0"))
    elif failure == "missing":
        bars.pop(10)
    elif failure == "cutoff":
        cutoff = FrozenCutoff(cutoff_at=h.freshness.finalizable_at)
    elif failure == "interval":
        h = replace(
            h,
            freshness=h.freshness.model_copy(
                update={
                    "expected": h.freshness.expected.model_copy(
                        update={"interval": BarInterval.FIVE_MINUTES}
                    )
                }
            ),
        )
    elif failure == "unavailable":
        h = replace(h, series=None)
    if h.series is not None:
        h = replace(h, series=replace(h.series, bars=tuple(bars)))
    r = CurrentPatternScanner().scan(pattern(), settings(), cutoff, [h])
    assert not r.matches and r.coverage.scanned == 0
    assert len(r.exclusions) == 1 and r.execution.hypotheses_evaluated == 0
    assert r.coverage.stale + r.coverage.skipped + r.coverage.failed == 1
    expected = {
        "stale": "stale_bar",
        "end": "mismatched_end",
        "unordered": "unordered_history",
        "duplicate": "malformed_history",
        "nan": "malformed_history",
        "zero": "malformed_history",
        "missing": "missing_required_observations",
        "cutoff": "cutoff_mismatch",
        "interval": "interval_mismatch",
        "unknown": "unknown_capability",
        "unavailable": "history_unavailable",
    }
    assert r.exclusions[0].reason == expected[failure]


def test_budget_drops_provisional_best_and_retains_finished_stocks() -> None:
    p = pattern([(0, 0), (1, 1)])
    values = prices([(0, 0), (1, 1)], 21)
    histories = [synthetic_history(s, values) for s in ["AAA", "BBB", "CCC"]]
    cutoff = FrozenCutoff(cutoff_at=histories[0].freshness.cutoff_at)
    r = CurrentPatternScanner(max_hypotheses=12, batch_size=4).scan(
        p, settings(), cutoff, histories
    )
    assert [m.symbol for m in r.matches] == ["AAA"]
    assert r.execution.hypotheses_evaluated == 12
    assert r.coverage.scanned == 1 and r.coverage.not_evaluated == 2
    assert r.coverage.evaluation_incomplete == 1
    assert r.execution.stop_reasons == ("computation_budget",)
    exact = CurrentPatternScanner(max_hypotheses=11).scan(p, settings(), cutoff, histories[:1])
    assert exact.coverage.scanned == 1 and not exact.execution.partial


@pytest.mark.parametrize("reason", ["cancelled", "deadline"])
def test_injected_stop_between_batches_discards_stock(reason: str) -> None:
    calls = 0

    def tick() -> float:
        nonlocal calls
        calls += 1
        return float(calls)

    control = (
        ScanControl(cancelled=lambda: tick() >= 4)
        if reason == "cancelled"
        else ScanControl(deadline_at=4, monotonic=tick)
    )
    h = synthetic_history("AAA", prices([(0, 0), (1, 1)], 21))
    r = CurrentPatternScanner(batch_size=2).scan(
        pattern(), settings(), FrozenCutoff(cutoff_at=h.freshness.cutoff_at), [h], control=control
    )
    assert not r.matches and r.coverage.not_evaluated == 1
    assert r.coverage.evaluation_incomplete == 1 and r.execution.hypotheses_evaluated == 2
    assert r.execution.stop_reasons == (reason,)


def test_batching_has_identical_results_and_work_counts() -> None:
    values = prices([(0, 0), (0.5, 1), (1, -0.4)], 41)
    a = scan(values, batch_size=1)
    b = scan(values, batch_size=256)
    assert a.matches == b.matches
    assert a.execution == b.execution


def test_normalization_precedes_numerical_tolerance() -> None:
    tiny = pattern([(0, 0), (0.5, 1e-200), (1, 0)])
    prepared = prepare_prefix(tiny, Fraction(1))
    normal = prepare_prefix(pattern(), Fraction(1))
    assert normal is not None and prepared is not None
    assert score_pairs([(normal, prepared)])[0].overall == pytest.approx(100, abs=1e-8)
    # A narrow needle missed by all 64 samples is numerically degenerate.
    assert prepare_prefix(pattern([(0, 0), (1e-5, 1), (2e-5, 0), (1, 0)]), Fraction(1)) is None
    logs = log_close_path([Decimal("1"), Decimal("1.000000000000000000000001"), Decimal("1")])
    assert prepare_candidate(logs) is not None
    assert math.isfinite(log_close_path([Decimal(1), Decimal("1e1000")])[1])
    with pytest.raises(ValueError):
        log_close_path([Decimal("NaN")])


@pytest.mark.parametrize(
    "provenance",
    [
        {"kind": "drawing"},
        {"kind": "text", "grammar_version": 1},
        {"kind": "screenshot_trace"},
        {
            "kind": "stock_section",
            "source_symbol": "AAA",
            "source_interval": "1day",
            "source_start_at": "2025-01-01T00:00:00Z",
            "source_end_at": "2025-01-02T00:00:00Z",
        },
    ],
)
def test_same_scoring_for_all_provenance(provenance: dict[str, object]) -> None:
    data = pattern().model_dump()
    data["provenance"] = provenance
    values = prices([(0, 0), (0.5, 1), (1, 0)], 21)
    r = scan(values, CanonicalPattern.model_validate(data), settings(match_type="full"))
    assert r.matches[0].similarity_score == pytest.approx(100, abs=1e-8)
    assert r.matches[0].components.amplitude is None


def test_zero_budget_and_pre_cancelled_work_are_not_ranked() -> None:
    values = prices([(0, 0), (1, 1)], 21)
    r = scan(values, max_hypotheses=0)
    assert not r.matches and r.execution.hypotheses_evaluated == 0
    assert r.coverage.not_evaluated == r.coverage.evaluation_incomplete == 1
    h = synthetic_history("AAA", values)
    r = CurrentPatternScanner().scan(
        pattern(),
        settings(),
        FrozenCutoff(cutoff_at=h.freshness.cutoff_at),
        [h],
        control=ScanControl(cancelled=lambda: True),
    )
    assert r.coverage.evaluation_incomplete == 0 and r.coverage.not_evaluated == 1
    with pytest.raises(ValueError):
        CurrentPatternScanner(batch_size=257)
    with pytest.raises(ValueError):
        CurrentPatternScanner(max_hypotheses=1_000_001)
