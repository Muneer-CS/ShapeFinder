import math
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation, localcontext

from shape_finder.core.similarity import PriceValue, SimilarityScore

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
        reference_path = _log_relative_path(reference)
        candidate_path = _log_relative_path(candidate)
        reference_aligned = _resample(reference_path, _POINTS)
        candidate_aligned = _resample(candidate_path, _POINTS)

        reference_amplitude = _rms(_center(reference_aligned))
        candidate_amplitude = _rms(_center(candidate_aligned))
        reference_flat = reference_amplitude <= _FLAT_TOLERANCE
        candidate_flat = candidate_amplitude <= _FLAT_TOLERANCE

        if reference_flat and candidate_flat:
            return SimilarityScore(100.0, 100.0, 100.0, 100.0, 100.0)
        if reference_flat or candidate_flat:
            return SimilarityScore(0.0, 0.0, 0.0, 0.0, 0.0)

        reference_shape = _unit_rms(_center(reference_aligned))
        candidate_shape = _unit_rms(_center(candidate_aligned))
        shape_score = _correlation_score(_cosine(reference_shape, candidate_shape))

        reference_slopes = _differences(reference_shape)
        candidate_slopes = _differences(candidate_shape)
        direction_score = _correlation_score(_cosine(reference_slopes, candidate_slopes))

        fitted_scale = max(
            0.0,
            _dot(reference_shape, candidate_shape) / _dot(candidate_shape, candidate_shape),
        )
        fitted_error = _rms(
            [
                left - fitted_scale * right
                for left, right in zip(reference_shape, candidate_shape, strict=True)
            ]
        )
        error_score = 100.0 * math.exp(-2.0 * fitted_error)

        amplitude_ratio = min(reference_amplitude, candidate_amplitude) / max(
            reference_amplitude, candidate_amplitude
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
