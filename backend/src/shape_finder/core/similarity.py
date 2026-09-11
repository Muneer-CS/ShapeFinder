from collections.abc import Sequence
from typing import Protocol

from shape_finder.core.market_data import PriceBar


class SimilarityEngine(Protocol):
    """Future isolated numerical engine; no implementation exists in Phase 1."""

    def compare(self, reference: Sequence[PriceBar], candidate: Sequence[PriceBar]) -> float: ...
