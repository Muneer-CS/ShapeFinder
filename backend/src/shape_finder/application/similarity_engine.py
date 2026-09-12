import math
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation, localcontext
from functools import lru_cache
from typing import cast

import numpy as np
from numpy.typing import NDArray

from shape_finder.core.similarity import (
    PreparedSimilaritySeries,
    PriceValue,
    SimilarityScore,
)

_POINTS = 64
_FLAT_TOLERANCE = 1e-8
_WEIGHTS = {
    "shape": 0.45,
    "direction": 0.30,
    "error": 0.20,
    "amplitude": 0.05,
}


class InvalidSimilarityInputError(ValueError):
    """Raised when a comparison cannot produce a meaningful finite result."""


class ChartSimilarityEngine:
    """Compare close-price paths without depending on price level or sampling density."""

    def compare(
        self, reference: Sequence[PriceValue], candidate: Sequence[PriceValue]
    ) -> SimilarityScore:
        return self.compare_prepared(self.prepare(reference), candidate)

    def prepare(self, values: Sequence[PriceValue]) -> PreparedSimilaritySeries:
        aligned = _resample(_log_relative_path(values), _POINTS)
        centered = _center(aligned)
        amplitude = _rms(centered)
        flat = amplitude <= _FLAT_TOLERANCE
        shape = [] if flat else _unit_rms(centered)
        return PreparedSimilaritySeries(
            aligned=tuple(aligned),
            amplitude=amplitude,
            flat=flat,
            shape=tuple(shape),
            slopes=tuple(_differences(shape)),
        )

    def compare_prepared(
        self,
        reference: PreparedSimilaritySeries,
        candidate: Sequence[PriceValue],
    ) -> SimilarityScore:
        prepared_candidate = self.prepare(candidate)
        if reference.flat and prepared_candidate.flat:
            return SimilarityScore(100.0, 100.0, 100.0, 100.0, 100.0)
        if reference.flat or prepared_candidate.flat:
            return SimilarityScore(0.0, 0.0, 0.0, 0.0, 0.0)

        shape_score = _correlation_score(_cosine(reference.shape, prepared_candidate.shape))
        direction_score = _correlation_score(_cosine(reference.slopes, prepared_candidate.slopes))

        fitted_scale = max(
            0.0,
            _dot(reference.shape, prepared_candidate.shape)
            / _dot(prepared_candidate.shape, prepared_candidate.shape),
        )
        fitted_error = _rms(
            [
                left - fitted_scale * right
                for left, right in zip(reference.shape, prepared_candidate.shape, strict=True)
            ]
        )
        error_score = 100.0 * math.exp(-2.0 * fitted_error)

        amplitude_ratio = min(reference.amplitude, prepared_candidate.amplitude) / max(
            reference.amplitude, prepared_candidate.amplitude
        )
        amplitude_score = 100.0 * amplitude_ratio

        overall = (
            _WEIGHTS["shape"] * shape_score
            + _WEIGHTS["direction"] * direction_score
            + _WEIGHTS["error"] * error_score
            + _WEIGHTS["amplitude"] * amplitude_score
        )
        return SimilarityScore(
            overall_score=_bounded(overall),
            shape_score=_bounded(shape_score),
            direction_score=_bounded(direction_score),
            error_score=_bounded(error_score),
            amplitude_score=_bounded(amplitude_score),
        )

    def compare_many(
        self,
        reference: PreparedSimilaritySeries,
        candidates: Sequence[Sequence[PriceValue]],
    ) -> tuple[SimilarityScore, ...]:
        """Compare equal-length windows in bounded NumPy batches.

        Irregular, invalid, boolean, or extreme inputs deliberately fall back to the
        canonical scalar implementation so its validation and Decimal semantics remain
        the single-pair contract.
        """
        array_input = isinstance(candidates, np.ndarray)
        rows = tuple(candidates)
        if not rows:
            return ()
        if not array_input and any(type(value) is bool for row in rows for value in row):
            return tuple(self.compare_prepared(reference, row) for row in rows)
        try:
            raw = np.asarray(rows)
            if raw.dtype.kind == "b":
                raise ValueError
            values = np.asarray(raw, dtype=np.float64)
        except (TypeError, ValueError, OverflowError):
            return tuple(self.compare_prepared(reference, row) for row in rows)
        if (
            values.ndim != 2
            or values.shape[1] < 2
            or not np.all(np.isfinite(values))
            or not np.all(values > 0)
        ):
            return tuple(self.compare_prepared(reference, row) for row in rows)

        left, right, fraction = _interpolation_plan(values.shape[1])
        paths = np.log(values) - np.log(values[:, :1])
        aligned = paths[:, left] * (1.0 - fraction) + paths[:, right] * fraction
        centered = aligned - np.mean(aligned, axis=1, keepdims=True)
        amplitudes = np.sqrt(np.mean(centered * centered, axis=1))
        flats = amplitudes <= _FLAT_TOLERANCE
        results: list[SimilarityScore | None] = [None] * len(rows)

        both_flat = flats if reference.flat else np.zeros_like(flats)
        for index in np.flatnonzero(both_flat):
            results[int(index)] = SimilarityScore(100.0, 100.0, 100.0, 100.0, 100.0)
        incompatible_flat = ~flats if reference.flat else flats
        for index in np.flatnonzero(incompatible_flat):
            results[int(index)] = SimilarityScore(0.0, 0.0, 0.0, 0.0, 0.0)

        active = np.array([], dtype=np.intp) if reference.flat else np.flatnonzero(~flats)
        if active.size:
            candidate_shapes = centered[active] / amplitudes[active, None]
            reference_shape = np.asarray(reference.shape, dtype=np.float64)
            reference_slopes = np.asarray(reference.slopes, dtype=np.float64)
            candidate_slopes = np.diff(candidate_shapes, axis=1)

            shape_cosines = _batch_cosine(candidate_shapes, reference_shape)
            direction_cosines = _batch_cosine(candidate_slopes, reference_slopes)
            shape_scores = 50.0 * (shape_cosines + 1.0)
            direction_scores = 50.0 * (direction_cosines + 1.0)

            candidate_norms = np.sum(candidate_shapes * candidate_shapes, axis=1)
            fitted_scales = np.maximum(0.0, (candidate_shapes @ reference_shape) / candidate_norms)
            residuals = reference_shape - fitted_scales[:, None] * candidate_shapes
            fitted_errors = np.sqrt(np.mean(residuals * residuals, axis=1))
            error_scores = 100.0 * np.exp(-2.0 * fitted_errors)
            amplitude_scores = 100.0 * (
                np.minimum(reference.amplitude, amplitudes[active])
                / np.maximum(reference.amplitude, amplitudes[active])
            )
            overall_scores = (
                _WEIGHTS["shape"] * shape_scores
                + _WEIGHTS["direction"] * direction_scores
                + _WEIGHTS["error"] * error_scores
                + _WEIGHTS["amplitude"] * amplitude_scores
            )
            for position, index in enumerate(active):
                results[int(index)] = SimilarityScore(
                    overall_score=_bounded(float(overall_scores[position])),
                    shape_score=_bounded(float(shape_scores[position])),
                    direction_score=_bounded(float(direction_scores[position])),
                    error_score=_bounded(float(error_scores[position])),
                    amplitude_score=_bounded(float(amplitude_scores[position])),
                )
        return tuple(result for result in results if result is not None)


@lru_cache(maxsize=32)
def _interpolation_plan(
    length: int,
) -> tuple[NDArray[np.intp], NDArray[np.intp], NDArray[np.float64]]:
    positions = np.arange(_POINTS, dtype=np.float64) * (length - 1) / (_POINTS - 1)
    left = np.floor(positions).astype(np.intp)
    right = np.minimum(left + 1, length - 1)
    return left, right, positions - left


def _batch_cosine(
    candidates: NDArray[np.float64], reference: NDArray[np.float64]
) -> NDArray[np.float64]:
    numerators = candidates @ reference
    denominators = np.sqrt(np.sum(candidates * candidates, axis=1) * np.sum(reference * reference))
    values = np.divide(
        numerators,
        denominators,
        out=np.zeros_like(numerators),
        where=denominators > _FLAT_TOLERANCE,
    )
    return cast(NDArray[np.float64], np.clip(values, -1.0, 1.0))


def _log_relative_path(values: Sequence[PriceValue]) -> list[float]:
    if len(values) < 2:
        raise InvalidSimilarityInputError("At least two close prices are required.")

    decimals: list[Decimal] = []
    for value in values:
        if isinstance(value, bool):
            raise InvalidSimilarityInputError("Close prices must be finite positive numbers.")
        try:
            number = value if isinstance(value, Decimal) else Decimal(str(value))
        except (InvalidOperation, ValueError):
            raise InvalidSimilarityInputError(
                "Close prices must be finite positive numbers."
            ) from None
        if not number.is_finite() or number <= 0:
            raise InvalidSimilarityInputError("Close prices must be finite positive numbers.")
        decimals.append(number)

    try:
        floats = [float(number) for number in decimals]
        if all(math.isfinite(number) and number > 0 for number in floats):
            first_log = math.log(floats[0])
            path = [math.log(number) - first_log for number in floats]
        else:
            with localcontext() as context:
                context.prec = 50
                first_log_decimal = decimals[0].ln()
                path = [float(number.ln() - first_log_decimal) for number in decimals]
    except (InvalidOperation, OverflowError, ValueError):
        raise InvalidSimilarityInputError("Close prices must be numerically comparable.") from None
    if not all(math.isfinite(value) for value in path):
        raise InvalidSimilarityInputError("Close prices must be numerically comparable.")
    return path


def _resample(values: Sequence[float], points: int) -> list[float]:
    last = len(values) - 1
    result: list[float] = []
    for index in range(points):
        position = index * last / (points - 1)
        left = int(math.floor(position))
        right = min(left + 1, last)
        fraction = position - left
        result.append(values[left] * (1.0 - fraction) + values[right] * fraction)
    return result


def _center(values: Sequence[float]) -> list[float]:
    mean = sum(values) / len(values)
    return [value - mean for value in values]


def _unit_rms(values: Sequence[float]) -> list[float]:
    scale = _rms(values)
    return [value / scale for value in values]


def _differences(values: Sequence[float]) -> list[float]:
    return [right - left for left, right in zip(values, values[1:], strict=False)]


def _rms(values: Sequence[float]) -> float:
    return math.sqrt(_dot(values, values) / len(values))


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return math.fsum(a * b for a, b in zip(left, right, strict=True))


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    denominator = math.sqrt(_dot(left, left) * _dot(right, right))
    if denominator <= _FLAT_TOLERANCE:
        both_flat = _rms(left) <= _FLAT_TOLERANCE and _rms(right) <= _FLAT_TOLERANCE
        return 1.0 if both_flat else 0.0
    return max(-1.0, min(1.0, _dot(left, right) / denominator))


def _correlation_score(correlation: float) -> float:
    return 50.0 * (correlation + 1.0)


def _bounded(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 6)
