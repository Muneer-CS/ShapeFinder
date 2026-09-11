from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, TypeAlias

PriceValue: TypeAlias = Decimal | float | int


@dataclass(frozen=True, slots=True)
class SimilarityScore:
    """Interpretable, bounded components of one deterministic comparison."""

    overall_score: float
    shape_score: float
    direction_score: float
    error_score: float
    amplitude_score: float


class SimilarityEngine(Protocol):
    """Provider- and transport-independent close-price comparison boundary."""

    def compare(
        self, reference: Sequence[PriceValue], candidate: Sequence[PriceValue]
    ) -> SimilarityScore: ...
