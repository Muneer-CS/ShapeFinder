import math
import random
from collections.abc import Sequence
from decimal import Decimal

import pytest

from shape_finder.application.similarity_engine import (
    ChartSimilarityEngine,
    InvalidSimilarityInputError,
)

ENGINE = ChartSimilarityEngine()
REFERENCE = [100, 108, 116, 105, 99, 112, 121, 117, 129]


def score(candidate: Sequence[int | float | Decimal]) -> float:
    return ENGINE.compare(REFERENCE, candidate).overall_score


def test_identical_series_scores_exactly_100_and_exposes_components() -> None:
    result = ENGINE.compare(REFERENCE, REFERENCE)

    assert result.overall_score == 100
    assert result.shape_score == 100
    assert result.direction_score == 100
    assert result.error_score == 100
    assert result.amplitude_score == 100


def test_absolute_price_scale_is_invariant() -> None:
    scaled = [value * 500 for value in REFERENCE]

    assert score(scaled) == pytest.approx(100, abs=1e-6)


def test_moderately_different_amplitude_remains_very_high() -> None:
    half_amplitude = [100 * ((value / 100) ** 0.5) for value in REFERENCE]

    result = ENGINE.compare(REFERENCE, half_amplitude)
    assert result.overall_score > 96
    assert 45 < result.amplitude_score < 55


def test_small_noise_preserves_a_high_score() -> None:
    rng = random.Random(41)
    noisy = [value * (1 + rng.uniform(-0.008, 0.008)) for value in REFERENCE]

    assert score(noisy) > 90


def test_wrong_turning_point_timing_is_penalized() -> None:
    close_match = [100, 108, 115, 106, 100, 111, 120, 118, 128]
    wrong_timing = [100, 105, 111, 119, 116, 106, 99, 112, 129]

    assert score(wrong_timing) < score(close_match)
    assert score(wrong_timing) < 85


def test_inverted_pattern_scores_low() -> None:
    inverted = [10000 / value for value in REFERENCE]

    assert score(inverted) < 20


def test_flat_series_policy_is_explicit_and_deterministic() -> None:
    assert ENGINE.compare([10, 10, 10], [500, 500, 500]).overall_score == 100
    assert ENGINE.compare([10, 10 + 1e-9, 10], [500, 500, 500]).overall_score == 100
    assert ENGINE.compare([10, 10, 10], [10, 11, 12]).overall_score == 0


def test_same_shape_at_different_lengths_aligns_well() -> None:
    dense = [
        100,
        104,
        108,
        112,
        116,
        111,
        105,
        102,
        99,
        105,
        112,
        116,
        121,
        119,
        117,
        123,
        129,
    ]

    assert score(dense) > 94


def test_missing_intermediate_points_remains_robust() -> None:
    with_points_missing = [100, 116, 105, 99, 121, 117, 129]

    assert score(with_points_missing) > 85


def test_two_point_series_is_valid_and_safe() -> None:
    result = ENGINE.compare([10, 11], [500, 550])

    assert result.overall_score == pytest.approx(100, abs=1e-6)


@pytest.mark.parametrize("values", [[], [1], [1, math.nan], [1, math.inf], [1, 0], [1, -1]])
def test_invalid_or_insufficient_input_is_rejected(values: list[float]) -> None:
    with pytest.raises(InvalidSimilarityInputError):
        ENGINE.compare(values, [1, 2])


def test_invalid_candidate_is_rejected() -> None:
    with pytest.raises(InvalidSimilarityInputError):
        ENGINE.compare([1, 2], [1, math.nan])


def test_extreme_decimal_scales_are_stable() -> None:
    tiny = [Decimal("1e-1000"), Decimal("1.1e-1000"), Decimal("0.9e-1000")]
    huge = [Decimal("1e1000"), Decimal("1.1e1000"), Decimal("0.9e1000")]

    assert ENGINE.compare(tiny, huge).overall_score == pytest.approx(100, abs=1e-6)


@pytest.mark.parametrize(
    ("reference", "near", "wrong_timing", "opposite"),
    [
        (
            REFERENCE,
            [100, 108, 115, 106, 100, 111, 120, 118, 128],
            [100, 105, 111, 119, 116, 106, 99, 112, 129],
            [10000 / value for value in REFERENCE],
        ),
        (
            [100, 110, 125, 118, 105],
            [100, 111, 124, 117, 106],
            [100, 106, 112, 118, 124],
            [125, 118, 105, 110, 125],
        ),
        (
            [100, 112, 124, 91, 103, 119],
            [100, 111, 123, 93, 104, 118],
            [100, 104, 108, 112, 116, 119],
            [119, 107, 95, 128, 116, 100],
        ),
    ],
)
def test_human_sanity_ranking(
    reference: list[int],
    near: list[int],
    wrong_timing: list[int],
    opposite: list[int | float],
) -> None:
    scores = [
        ENGINE.compare(reference, candidate).overall_score
        for candidate in (near, wrong_timing, opposite)
    ]
    assert scores[0] > scores[1] > scores[2]


def test_output_is_deterministic_and_bounded_across_varied_candidates() -> None:
    candidates = [
        REFERENCE,
        list(reversed(REFERENCE)),
        [100, 101, 99, 102, 98, 103, 97, 104, 96],
        [100, 130, 80, 140, 75, 150, 70, 160, 65],
    ]

    first = [ENGINE.compare(REFERENCE, candidate) for candidate in candidates]
    second = [ENGINE.compare(REFERENCE, candidate) for candidate in candidates]
    assert first == second
    for result in first:
        assert all(
            0 <= component <= 100
            for component in (
                result.overall_score,
                result.shape_score,
                result.direction_score,
                result.error_score,
                result.amplitude_score,
            )
        )


def test_prepared_reference_produces_identical_score() -> None:
    candidate = [100, 108, 115, 106, 100, 111, 120, 118, 128]
    prepared = ENGINE.prepare(REFERENCE)

    assert ENGINE.compare_prepared(prepared, candidate) == ENGINE.compare(REFERENCE, candidate)
