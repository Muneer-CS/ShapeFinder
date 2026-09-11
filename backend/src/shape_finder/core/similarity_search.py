from dataclasses import dataclass
from datetime import datetime

from shape_finder.core.market_data import BarInterval
from shape_finder.core.similarity import SimilarityScore


class InvalidSimilaritySearchError(ValueError):
    """Raised when a similarity scan request cannot be evaluated safely."""


@dataclass(frozen=True, slots=True)
class SimilaritySearchQuery:
    reference_symbol: str
    reference_start: datetime
    reference_end: datetime
    interval: BarInterval
    search_start: datetime
    search_end: datetime
    candidate_symbols: tuple[str, ...]
    top_n: int = 10
    minimum_similarity: float | None = None


@dataclass(frozen=True, slots=True)
class ReferenceSummary:
    symbol: str
    start: datetime
    end: datetime
    interval: BarInterval
    bar_count: int


@dataclass(frozen=True, slots=True)
class SimilarityMatch:
    symbol: str
    start: datetime
    end: datetime
    interval: BarInterval
    bar_count: int
    score: SimilarityScore


@dataclass(frozen=True, slots=True)
class ScanStatistics:
    symbols_requested: int
    symbols_scanned: int
    windows_evaluated: int
    windows_passing_threshold: int
    matches_returned: int


@dataclass(frozen=True, slots=True)
class SimilaritySearchResult:
    reference: ReferenceSummary
    search_start: datetime
    search_end: datetime
    matches: tuple[SimilarityMatch, ...]
    statistics: ScanStatistics
