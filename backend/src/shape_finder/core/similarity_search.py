from dataclasses import dataclass, field
from datetime import datetime

from shape_finder.core.market_data import BarInterval
from shape_finder.core.similarity import SimilarityScore
from shape_finder.core.universe import UniverseSelection


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
    candidate_symbols: tuple[str, ...] = ()
    universe: UniverseSelection | None = None
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
    universe_id: str = "custom"
    universe_symbols_total: int = 0
    symbols_eligible: int = 0
    symbols_skipped: int = 0
    symbols_failed: int = 0
    universe_stale: bool = False
    ready_before_hydration: int = 0
    hydration_limit: int = 0
    hydration_attempted: int = 0
    hydration_succeeded: int = 0
    hydration_failed: int = 0
    hydration_fetched: int = 0
    hydration_persisted: int = 0
    hydration_became_ready: int = 0
    hydration_suppressed: int = 0
    hydration_failure_counts: dict[str, int] = field(default_factory=dict)
    ready_after_hydration: int = 0
    provider_rate_limited: bool = False
    provider_daily_quota: bool = False
    hydration_provider_unavailable: bool = False
    hydration_timed_out: bool = False


@dataclass(frozen=True, slots=True)
class SimilaritySearchResult:
    reference: ReferenceSummary
    search_start: datetime
    search_end: datetime
    matches: tuple[SimilarityMatch, ...]
    statistics: ScanStatistics
