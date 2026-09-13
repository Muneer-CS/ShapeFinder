import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from time import perf_counter
from typing import cast

import numpy as np

from shape_finder.application.market_data_service import MarketDataService
from shape_finder.application.universe import UniverseService
from shape_finder.application.universe_hydration import UniverseHydrationService
from shape_finder.core.market_data import PriceBar, TimeSeries
from shape_finder.core.persistence import MarketDataRepository
from shape_finder.core.similarity import (
    BatchSimilarityEngine,
    PreparedSimilaritySeries,
    PriceValue,
    SimilarityEngine,
    SimilarityScore,
)
from shape_finder.core.similarity_search import (
    InvalidSimilaritySearchError,
    ReferenceSummary,
    ScanStatistics,
    SimilarityMatch,
    SimilaritySearchQuery,
    SimilaritySearchResult,
)
from shape_finder.core.universe import UniverseKind

MAX_CANDIDATE_SYMBOLS = 10
MAX_TOP_N = 100
MAX_UNIVERSE_SYMBOLS = 50_000
MAX_SCAN_WINDOWS = 2_000_000
_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
logger = logging.getLogger("shape_finder.similarity")


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
        batch_size: int = 4096,
        use_batch: bool = True,
    ) -> None:
        if stride < 1:
            raise InvalidSimilaritySearchError("Stride must be at least one bar.")
        if not 0 <= overlap_threshold <= 1 or not 0 <= self_overlap_threshold <= 1:
            raise InvalidSimilaritySearchError("Overlap thresholds must be between 0 and 1.")
        if batch_size < 1:
            raise InvalidSimilaritySearchError("Batch size must be at least one window.")
        self._engine = engine
        self._stride = stride
        self._overlap_threshold = overlap_threshold
        self._self_overlap_threshold = self_overlap_threshold
        self._batch_size = batch_size
        self._use_batch = use_batch

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
        finalists: list[_ScoredWindow] = []
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
            offsets = list(range(0, len(bars) - window_length + 1, self._stride))
            if candidate.symbol.upper() == reference.symbol.upper():
                offsets = [
                    offset
                    for offset in offsets
                    if _overlap_ratio(
                        frozenset(bar.timestamp for bar in bars[offset : offset + window_length]),
                        reference_timestamps,
                    )
                    <= self._self_overlap_threshold
                ]
            scored = self._score_windows(prepared_reference, bars, window_length, offsets)
            windows_evaluated += len(scored)
            candidate_ranked: list[_ScoredWindow] = []
            for offset, score in scored:
                if score.overall_score < threshold:
                    continue
                windows_passing += 1
                window = bars[offset : offset + window_length]
                timestamps = frozenset(bar.timestamp for bar in window)
                candidate_ranked.append(
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
            candidate_ranked.sort(key=_ranking_key)
            candidate_selected: list[_ScoredWindow] = []
            for item in candidate_ranked:
                if any(
                    _overlap_ratio(item.timestamps, chosen.timestamps) > self._overlap_threshold
                    for chosen in candidate_selected
                ):
                    continue
                candidate_selected.append(item)
                if len(candidate_selected) == query.top_n:
                    break
            finalists.extend(candidate_selected)

        finalists.sort(
            key=lambda item: (
                -item.match.score.overall_score,
                item.match.start,
                item.match.symbol,
                item.match.end,
            )
        )
        matches = tuple(item.match for item in finalists[: query.top_n])
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

    def _score_windows(
        self,
        reference: PreparedSimilaritySeries,
        bars: Sequence[PriceBar],
        window_length: int,
        offsets: Sequence[int],
    ) -> list[tuple[int, SimilarityScore]]:
        if not offsets:
            return []
        if self._use_batch and isinstance(self._engine, BatchSimilarityEngine):
            closes = np.fromiter((float(bar.close) for bar in bars), dtype=np.float64)
            if np.all(np.isfinite(closes)) and np.all(closes > 0):
                views = np.lib.stride_tricks.sliding_window_view(closes, window_length)
                results: list[tuple[int, SimilarityScore]] = []
                for start in range(0, len(offsets), self._batch_size):
                    batch_offsets = offsets[start : start + self._batch_size]
                    windows = views[np.asarray(batch_offsets, dtype=np.intp)]
                    scores = self._engine.compare_many(
                        reference,
                        cast(Sequence[Sequence[PriceValue]], windows),
                    )
                    results.extend(zip(batch_offsets, scores, strict=True))
                return results
        return [
            (
                offset,
                self._engine.compare_prepared(
                    reference,
                    tuple(bar.close for bar in bars[offset : offset + window_length]),
                ),
            )
            for offset in offsets
        ]


class SimilaritySearchService:
    """Load complete data through the cache-aware service, then scan it once in memory."""

    def __init__(
        self,
        market_data: MarketDataService,
        scanner: HistoricalSimilarityScanner,
        repository: MarketDataRepository | None = None,
        universe_service: UniverseService | None = None,
        hydration_service: UniverseHydrationService | None = None,
    ) -> None:
        self._market_data = market_data
        self._scanner = scanner
        self._repository = repository
        self._universe_service = universe_service
        self._hydration_service = hydration_service

    async def search(self, query: SimilaritySearchQuery) -> SimilaritySearchResult:
        started = perf_counter()
        normalized = _normalized_query(query)
        logger.info(
            "scan_start reference_symbol=%s interval=%s universe=%s candidates=%s",
            normalized.reference_symbol,
            normalized.interval.value,
            normalized.universe.kind.value if normalized.universe else "custom",
            len(normalized.candidate_symbols),
        )
        reference = await self._market_data.get_time_series(
            normalized.reference_symbol,
            normalized.reference_start,
            normalized.reference_end,
            normalized.interval,
        )
        if normalized.universe and normalized.universe.kind is not UniverseKind.CUSTOM:
            return await self._search_universe(normalized, reference, started)
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
        result = await asyncio.to_thread(self._scanner.scan, reference, candidates, normalized)
        total = len(normalized.candidate_symbols)
        final = replace(
            result,
            statistics=replace(
                result.statistics,
                universe_id=UniverseKind.CUSTOM.value,
                universe_symbols_total=total,
                symbols_eligible=total,
                symbols_skipped=0,
            ),
        )
        self._log_complete(final, started)
        return final

    async def _search_universe(
        self, query: SimilaritySearchQuery, reference: TimeSeries, started: float
    ) -> SimilaritySearchResult:
        if self._repository is None or self._universe_service is None or query.universe is None:
            raise InvalidSimilaritySearchError("Broad-universe search is not configured.")
        hydration = None
        if self._hydration_service is not None:
            hydration = await self._hydration_service.hydrate(
                query.universe.kind,
                query.search_start,
                query.search_end,
                query.interval,
                len(tuple(bar for bar in reference.bars if _valid_bar(bar))),
            )
            symbols = hydration.symbols
            eligible = hydration.ready_after
            stale = hydration.universe_stale
        else:
            symbols, _, stale = await self._universe_service.resolve(query.universe.kind)
            reference_bars = tuple(bar for bar in reference.bars if _valid_bar(bar))
            eligible = tuple(
                await self._repository.get_scan_ready_symbols(
                    symbols,
                    query.interval,
                    query.search_start,
                    query.search_end,
                    len(reference_bars),
                )
            )
        if len(symbols) > MAX_UNIVERSE_SYMBOLS:
            raise InvalidSimilaritySearchError(
                f"The selected universe exceeds the {MAX_UNIVERSE_SYMBOLS}-symbol safety limit."
            )
        reference_bars = tuple(bar for bar in reference.bars if _valid_bar(bar))
        estimated_bars = _estimated_periods(query.search_start, query.search_end, query.interval)
        estimated_windows = len(eligible) * max(0, estimated_bars - len(reference_bars) + 1)
        if estimated_windows > MAX_SCAN_WINDOWS:
            raise InvalidSimilaritySearchError(
                f"The scan could exceed the {MAX_SCAN_WINDOWS:,}-window safety limit. "
                "Use a shorter date range or a narrower universe."
            )
        candidates = [
            await self._repository.get_time_series(
                symbol, query.interval, query.search_start, query.search_end
            )
            for symbol in eligible
        ]
        resolved_query = replace(query, candidate_symbols=symbols)
        result = await asyncio.to_thread(self._scanner.scan, reference, candidates, resolved_query)
        final = replace(
            result,
            statistics=replace(
                result.statistics,
                symbols_requested=len(symbols),
                universe_id=query.universe.kind.value,
                universe_symbols_total=len(symbols),
                symbols_eligible=len(eligible),
                symbols_skipped=len(symbols) - len(eligible),
                symbols_failed=0,
                universe_stale=stale,
                ready_before_hydration=(
                    len(hydration.ready_before) if hydration is not None else len(eligible)
                ),
                hydration_limit=(hydration.hydration_limit if hydration is not None else 0),
                hydration_attempted=(hydration.attempted if hydration is not None else 0),
                hydration_succeeded=(hydration.succeeded if hydration is not None else 0),
                hydration_failed=(hydration.failed if hydration is not None else 0),
                hydration_fetched=(hydration.fetched if hydration is not None else 0),
                hydration_persisted=(hydration.persisted if hydration is not None else 0),
                hydration_became_ready=(hydration.became_ready if hydration is not None else 0),
                hydration_suppressed=(hydration.suppressed if hydration is not None else 0),
                hydration_failure_counts=(
                    hydration.failure_counts if hydration is not None else {}
                ),
                ready_after_hydration=len(eligible),
                provider_rate_limited=(
                    hydration.provider_rate_limited if hydration is not None else False
                ),
                provider_daily_quota=(
                    hydration.provider_daily_quota if hydration is not None else False
                ),
                hydration_provider_unavailable=(
                    hydration.provider_unavailable if hydration is not None else False
                ),
                hydration_timed_out=(hydration.timed_out if hydration is not None else False),
            ),
        )
        self._log_complete(final, started)
        return final

    @staticmethod
    def _log_complete(result: SimilaritySearchResult, started: float) -> None:
        logger.info(
            "scan_end symbols_scanned=%s windows=%s matches=%s duration_ms=%.2f",
            result.statistics.symbols_scanned,
            result.statistics.windows_evaluated,
            result.statistics.matches_returned,
            (perf_counter() - started) * 1000,
        )


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
        universe=query.universe,
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
    is_custom = query.universe is None or query.universe.kind is UniverseKind.CUSTOM
    if is_custom and not query.candidate_symbols:
        raise InvalidSimilaritySearchError("At least one candidate symbol is required.")
    if is_custom and len(query.candidate_symbols) > MAX_CANDIDATE_SYMBOLS:
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


def _ranking_key(item: _ScoredWindow) -> tuple[float, datetime, str, datetime]:
    return (
        -item.match.score.overall_score,
        item.match.start,
        item.match.symbol,
        item.match.end,
    )


def _estimated_periods(start: datetime, end: datetime, interval: object) -> int:
    steps = {
        "1min": timedelta(minutes=1),
        "5min": timedelta(minutes=5),
        "15min": timedelta(minutes=15),
        "30min": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "1day": timedelta(days=1),
        "1week": timedelta(weeks=1),
    }
    value = getattr(interval, "value", str(interval))
    return int((end - start) / steps[value]) + 1
