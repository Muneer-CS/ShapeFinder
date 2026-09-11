import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from shape_finder.application.market_data_service import MarketDataService
from shape_finder.core.market_data import PriceBar, TimeSeries
from shape_finder.core.similarity import SimilarityEngine
from shape_finder.core.similarity_search import (
    InvalidSimilaritySearchError,
    ReferenceSummary,
    ScanStatistics,
    SimilarityMatch,
    SimilaritySearchQuery,
    SimilaritySearchResult,
)

MAX_CANDIDATE_SYMBOLS = 10
MAX_TOP_N = 100
_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")


@dataclass(frozen=True, slots=True)
class _ScoredWindow:
    match: SimilarityMatch
    timestamps: frozenset[datetime]


class HistoricalSimilarityScanner:
    """Generate, score, rank, and de-duplicate in-memory candidate windows."""

    def __init__(
        self,
        engine: SimilarityEngine,
        *,
        stride: int = 1,
        overlap_threshold: float = 0.5,
        self_overlap_threshold: float = 0.5,
    ) -> None:
        if stride < 1:
            raise InvalidSimilaritySearchError("Stride must be at least one bar.")
        if not 0 <= overlap_threshold <= 1 or not 0 <= self_overlap_threshold <= 1:
            raise InvalidSimilaritySearchError("Overlap thresholds must be between 0 and 1.")
        self._engine = engine
        self._stride = stride
        self._overlap_threshold = overlap_threshold
        self._self_overlap_threshold = self_overlap_threshold

    def scan(
        self,
        reference: TimeSeries,
        candidates: Sequence[TimeSeries],
        query: SimilaritySearchQuery,
    ) -> SimilaritySearchResult:
        _validate_query(query)
        reference_bars = tuple(bar for bar in reference.bars if _valid_bar(bar))
        if reference.interval is not query.interval:
            raise InvalidSimilaritySearchError("Reference interval does not match the request.")
        if len(reference_bars) < 2:
            raise InvalidSimilaritySearchError("Reference data requires at least two valid bars.")

        reference_closes = tuple(bar.close for bar in reference_bars)
        prepared_reference = self._engine.prepare(reference_closes)
        reference_timestamps = frozenset(bar.timestamp for bar in reference_bars)
        window_length = len(reference_bars)
        threshold = query.minimum_similarity if query.minimum_similarity is not None else 0.0
        ranked: list[_ScoredWindow] = []
        windows_evaluated = 0
        windows_passing = 0

        for candidate in candidates:
            if candidate.interval is not query.interval:
                raise InvalidSimilaritySearchError(
                    f"Candidate interval for {candidate.symbol} does not match the reference."
                )
            bars = tuple(
                bar
                for bar in sorted(candidate.bars, key=lambda item: item.timestamp)
                if query.search_start <= bar.timestamp <= query.search_end and _valid_bar(bar)
            )
            if len(bars) < window_length:
                continue
            for offset in range(0, len(bars) - window_length + 1, self._stride):
                window = bars[offset : offset + window_length]
                timestamps = frozenset(bar.timestamp for bar in window)
                if (
                    candidate.symbol.upper() == reference.symbol.upper()
                    and _overlap_ratio(timestamps, reference_timestamps)
                    > self._self_overlap_threshold
                ):
                    continue
                score = self._engine.compare_prepared(
                    prepared_reference, tuple(bar.close for bar in window)
                )
                windows_evaluated += 1
                if score.overall_score < threshold:
                    continue
                windows_passing += 1
                ranked.append(
                    _ScoredWindow(
                        match=SimilarityMatch(
                            symbol=candidate.symbol.upper(),
                            start=window[0].timestamp,
                            end=window[-1].timestamp,
                            interval=candidate.interval,
                            bar_count=window_length,
                            score=score,
                        ),
                        timestamps=timestamps,
                    )
                )

        ranked.sort(
            key=lambda item: (
                -item.match.score.overall_score,
                item.match.start,
                item.match.symbol,
                item.match.end,
            )
        )
        selected: list[_ScoredWindow] = []
        for item in ranked:
            if any(
                chosen.match.symbol == item.match.symbol
                and _overlap_ratio(item.timestamps, chosen.timestamps) > self._overlap_threshold
                for chosen in selected
            ):
                continue
            selected.append(item)
            if len(selected) == query.top_n:
                break

        matches = tuple(item.match for item in selected)
        return SimilaritySearchResult(
            reference=ReferenceSummary(
                symbol=reference.symbol.upper(),
                start=reference_bars[0].timestamp,
                end=reference_bars[-1].timestamp,
                interval=reference.interval,
                bar_count=window_length,
            ),
            search_start=query.search_start,
            search_end=query.search_end,
            matches=matches,
            statistics=ScanStatistics(
                symbols_requested=len(query.candidate_symbols),
                symbols_scanned=len(candidates),
                windows_evaluated=windows_evaluated,
                windows_passing_threshold=windows_passing,
                matches_returned=len(matches),
            ),
        )


class SimilaritySearchService:
    """Load complete data through the cache-aware service, then scan it once in memory."""

    def __init__(
        self,
        market_data: MarketDataService,
        scanner: HistoricalSimilarityScanner,
    ) -> None:
        self._market_data = market_data
        self._scanner = scanner

    async def search(self, query: SimilaritySearchQuery) -> SimilaritySearchResult:
        normalized = _normalized_query(query)
        reference = await self._market_data.get_time_series(
            normalized.reference_symbol,
            normalized.reference_start,
            normalized.reference_end,
            normalized.interval,
        )
        candidates: list[TimeSeries] = []
        for symbol in normalized.candidate_symbols:
            candidates.append(
                await self._market_data.get_time_series(
                    symbol,
                    normalized.search_start,
                    normalized.search_end,
                    normalized.interval,
                )
            )
        return self._scanner.scan(reference, candidates, normalized)


def _normalized_query(query: SimilaritySearchQuery) -> SimilaritySearchQuery:
    symbols = tuple(dict.fromkeys(_normalize_symbol(symbol) for symbol in query.candidate_symbols))
    normalized = SimilaritySearchQuery(
        reference_symbol=_normalize_symbol(query.reference_symbol),
        reference_start=query.reference_start,
        reference_end=query.reference_end,
        interval=query.interval,
        search_start=query.search_start,
        search_end=query.search_end,
        candidate_symbols=symbols,
        top_n=query.top_n,
        minimum_similarity=query.minimum_similarity,
    )
    _validate_query(normalized)
    return normalized


def _validate_query(query: SimilaritySearchQuery) -> None:
    if query.reference_start.tzinfo is None or query.reference_end.tzinfo is None:
        raise InvalidSimilaritySearchError("Reference timestamps must include a timezone.")
    if query.search_start.tzinfo is None or query.search_end.tzinfo is None:
        raise InvalidSimilaritySearchError("Search timestamps must include a timezone.")
    if query.reference_start >= query.reference_end:
        raise InvalidSimilaritySearchError("Reference start must be earlier than reference end.")
    if query.search_start >= query.search_end:
        raise InvalidSimilaritySearchError("Search start must be earlier than search end.")
    if not query.candidate_symbols:
        raise InvalidSimilaritySearchError("At least one candidate symbol is required.")
    if len(query.candidate_symbols) > MAX_CANDIDATE_SYMBOLS:
        raise InvalidSimilaritySearchError(
            f"At most {MAX_CANDIDATE_SYMBOLS} candidate symbols are allowed."
        )
    if not 1 <= query.top_n <= MAX_TOP_N:
        raise InvalidSimilaritySearchError(f"top_n must be between 1 and {MAX_TOP_N}.")
    if query.minimum_similarity is not None and not 0 <= query.minimum_similarity <= 100:
        raise InvalidSimilaritySearchError("minimum_similarity must be between 0 and 100.")


def _normalize_symbol(symbol: str) -> str:
    normalized = symbol.strip().upper()
    if not _SYMBOL_PATTERN.fullmatch(normalized):
        raise InvalidSimilaritySearchError("Stock symbols must use a supported format.")
    return normalized


def _valid_bar(bar: PriceBar) -> bool:
    return bar.close.is_finite() and bar.close > Decimal(0) and bar.timestamp.tzinfo is not None


def _overlap_ratio(left: frozenset[datetime], right: frozenset[datetime]) -> float:
    return len(left & right) / min(len(left), len(right))
