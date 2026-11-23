"""current_shape_v1: scale-free paths, legacy component mathematics, no amplitude."""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction

import numpy as np
from numpy.typing import NDArray

from shape_finder.core.pattern_scan import CanonicalPattern

_SAMPLES = 64
_DIRECTION_TOLERANCE = 1e-8  # Applied only after unit-RMS shape normalization.


@dataclass(frozen=True)
class PreparedShape:
    shape: NDArray[np.float64]
    slopes: NDArray[np.float64]


@dataclass(frozen=True)
class ShapeScore:
    overall: float
    shape: float
    direction: float
    error: float


def clipped_prefix(
    pattern: CanonicalPattern, completion: Fraction
) -> tuple[tuple[float, float], ...]:
    """Keep [0,p], inserting the interpolated endpoint, without consulting later extrema."""
    if not 0 < completion <= 1:
        raise ValueError("Completion must lie in (0,1].")
    p = float(completion)
    points: list[tuple[float, float]] = []
    for index, point in enumerate(pattern.points):
        if point.x <= p:
            points.append((point.x, point.y))
            if point.x == p:
                break
        else:
            left = pattern.points[index - 1]
            weight = (p - left.x) / (point.x - left.x)
            points.append((p, left.y + weight * (point.y - left.y)))
            break
    return tuple(points)


def _prepare(x: Sequence[float], y: Sequence[float]) -> PreparedShape | None:
    values = np.asarray(y, dtype=np.float64)
    span = float(np.max(values) - np.min(values))
    if span <= 0 or not math.isfinite(span):
        return None
    # Normalize BEFORE resampling and slope tolerances. This is equivalent to
    # taking log(exp(k * prefix_relative_range)), without exp/log roundoff.
    relative = (values - values[0]) / span
    aligned = np.interp(np.linspace(0, x[-1], _SAMPLES), x, relative)
    centered = aligned - np.mean(aligned)
    rms = float(np.sqrt(np.mean(centered * centered)))
    if not math.isfinite(rms) or rms <= 1e-12:
        return None  # Degenerate sampled shape, measured AFTER range normalization.
    shape = centered / rms
    slopes = np.diff(shape)
    shape.flags.writeable = False
    slopes.flags.writeable = False
    return PreparedShape(shape, slopes)


def prepare_prefix(pattern: CanonicalPattern, completion: Fraction) -> PreparedShape | None:
    points = clipped_prefix(pattern, completion)
    return _prepare([x for x, _ in points], [y for _, y in points])


def log_close_path(closes: Sequence[Decimal]) -> tuple[float, ...]:
    """One log transform; retain small relative movements and extreme Decimal prices."""
    if not closes or any(not p.is_finite() or p <= 0 for p in closes):
        raise ValueError("Closes must be finite and positive.")
    with localcontext() as context:
        context.prec = 50
        first = closes[0]
        result: list[float] = []
        for close in closes:
            relative = float((close - first) / first)
            value = (
                math.log1p(relative)
                if math.isfinite(relative) and relative > -1
                else float(close.ln() - first.ln())
            )
            if not math.isfinite(value):
                raise ValueError("Log closes are not numerically comparable.")
            result.append(value)
    return tuple(result)


def prepare_candidate(log_closes: Sequence[float]) -> PreparedShape | None:
    return _prepare(np.linspace(0, 1, len(log_closes)).tolist(), log_closes)


def _cosines(left: NDArray[np.float64], right: NDArray[np.float64]) -> NDArray[np.float64]:
    numerator = np.sum(left * right, axis=1)
    lnorm = np.sum(left * left, axis=1)
    rnorm = np.sum(right * right, axis=1)
    denominator = np.sqrt(lnorm * rnorm)
    cosine = np.divide(
        numerator,
        denominator,
        out=np.zeros_like(numerator),
        where=denominator > _DIRECTION_TOLERANCE,
    )
    both_flat = (np.sqrt(lnorm / left.shape[1]) <= _DIRECTION_TOLERANCE) & (
        np.sqrt(rnorm / right.shape[1]) <= _DIRECTION_TOLERANCE
    )
    cosine = np.where((denominator <= _DIRECTION_TOLERANCE) & both_flat, 1, cosine)
    return np.clip(cosine, -1, 1)


def score_pairs(pairs: Sequence[tuple[PreparedShape, PreparedShape]]) -> tuple[ShapeScore, ...]:
    """Caller supplies a bounded batch. Keep unrounded values for threshold decisions."""
    if not pairs:
        return ()
    left = np.stack([a.shape for a, _ in pairs])
    right = np.stack([b.shape for _, b in pairs])
    shape = 50 * (_cosines(left, right) + 1)
    direction = 50 * (
        _cosines(np.stack([a.slopes for a, _ in pairs]), np.stack([b.slopes for _, b in pairs])) + 1
    )
    scale = np.maximum(0, np.sum(left * right, axis=1) / np.sum(right * right, axis=1))
    residual = left - scale[:, None] * right
    error = 100 * np.exp(-2 * np.sqrt(np.mean(residual * residual, axis=1)))
    overall = np.clip((0.45 * shape + 0.30 * direction + 0.20 * error) / 0.95, 0, 100)
    return tuple(
        ShapeScore(float(o), float(s), float(d), float(e))
        for o, s, d, e in zip(overall, shape, direction, error, strict=True)
    )
