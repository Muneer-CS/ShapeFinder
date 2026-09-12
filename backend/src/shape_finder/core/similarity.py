from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, TypeAlias, runtime_checkable

PriceValue: TypeAlias = Decimal | float | int


@dataclass(frozen=True, slots=True)
class SimilarityScore:
    """Interpretable, bounded components of one deterministic comparison."""

    overall_score: float
    shape_score: float
    direction_score: float
    error_score: float
    amplitude_score: float


@dataclass(frozen=True, slots=True)
class PreparedSimilaritySeries:
    """Reusable engine-owned representation for repeated comparisons."""

    aligned: tuple[float, ...]
    amplitude: float
    flat: bool
    shape: tuple[float, ...]
    slopes: tuple[float, ...]


class SimilarityEngine(Protocol):
    """Provider- and transport-independent close-price comparison boundary."""

    def compare(
        self, reference: Sequence[PriceValue], candidate: Sequence[PriceValue]
    ) -> SimilarityScore: ...

    def prepare(self, values: Sequence[PriceValue]) -> PreparedSimilaritySeries: ...

    def compare_prepared(
        self,
        reference: PreparedSimilaritySeries,
        candidate: Sequence[PriceValue],
    ) -> SimilarityScore: ...


@runtime_checkable
class BatchSimilarityEngine(SimilarityEngine, Protocol):
    """Optional optimized boundary for equal-length candidate batches."""

    def compare_many(
        self,
        reference: PreparedSimilaritySeries,
        candidates: Sequence[Sequence[PriceValue]],
    ) -> tuple[SimilarityScore, ...]: ...
