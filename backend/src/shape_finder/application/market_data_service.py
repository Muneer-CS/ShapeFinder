import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from shape_finder.core.errors import InvalidDateRangeError, MissingApiKeyError
from shape_finder.core.market_data import BarInterval, MarketDataProvider, TimeSeries
from shape_finder.core.persistence import CoverageRange, MarketDataRepository

INTERVAL_DURATION = {
    BarInterval.ONE_MINUTE: timedelta(minutes=1),
    BarInterval.FIVE_MINUTES: timedelta(minutes=5),
    BarInterval.FIFTEEN_MINUTES: timedelta(minutes=15),
    BarInterval.THIRTY_MINUTES: timedelta(minutes=30),
    BarInterval.ONE_HOUR: timedelta(hours=1),
    BarInterval.ONE_DAY: timedelta(days=1),
    BarInterval.ONE_WEEK: timedelta(weeks=1),
}
logger = logging.getLogger("shape_finder.market_data")


class MarketDataService:
    """Coordinates local coverage, bounded provider fetches, and atomic persistence."""

    def __init__(
        self,
        provider: MarketDataProvider,
        repository: MarketDataRepository,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_points_per_chunk: int = 4_500,
        recent_edge_bars: int = 3,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._clock = clock
        self._max_points_per_chunk = max_points_per_chunk
        self._recent_edge_bars = recent_edge_bars
        self._refresh_locks: dict[tuple[str, BarInterval], asyncio.Lock] = {}

    async def get_time_series(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> TimeSeries:
        self._validate_range(start, end)
        symbol = symbol.upper()
        lock = self._refresh_locks.setdefault((symbol, interval), asyncio.Lock())
        waited_for_refresh = lock.locked()
        async with lock:
            return await self._get_time_series_locked(
                symbol, start, end, interval, refresh_recent=not waited_for_refresh
            )

    async def _get_time_series_locked(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
        *,
        refresh_recent: bool,
    ) -> TimeSeries:
        coverage = await self._repository.get_coverage(symbol, interval, start, end)
        missing = self._missing_ranges(start, end, interval, coverage)
        refresh = self._recent_refresh_range(start, end, interval) if refresh_recent else None
        requested = self._merge_ranges([*missing, *([refresh] if refresh else [])])
        chunks = [chunk for item in requested for chunk in self._chunk_range(*item, interval)]

        logger.info(
            "cache_lookup symbol=%s interval=%s coverage_ranges=%s fetch_chunks=%s",
            symbol,
            interval.value,
            len(coverage),
            len(chunks),
        )

        if chunks:
            synced_at = self._clock().astimezone(UTC)
            fetched: list[TimeSeries] = []
            fetched_coverage: list[CoverageRange] = []
            for chunk_start, chunk_end in chunks:
                logger.info(
                    "provider_fetch symbol=%s interval=%s start=%s end=%s",
                    symbol,
                    interval.value,
                    chunk_start.isoformat(),
                    chunk_end.isoformat(),
                )
                try:
                    item = await self._provider.get_historical_bars(
                        symbol=symbol,
                        start=chunk_start,
                        end=chunk_end,
                        interval=interval,
                    )
                except MissingApiKeyError:
                    if missing:
                        raise
                    return await self._repository.get_time_series(symbol, interval, start, end)
                fetched.append(item)
                fetched_coverage.append(
                    CoverageRange(start=chunk_start, end=chunk_end, synced_at=synced_at)
                )
            await self._repository.upsert_time_series(
                fetched, fetched_coverage, source="market-data-provider"
            )

        return await self._repository.get_time_series(symbol, interval, start, end)

    @staticmethod
    def _validate_range(start: datetime, end: datetime) -> None:
        if start.tzinfo is None or end.tzinfo is None:
            raise InvalidDateRangeError("Start and end timestamps must include a timezone.")
        if start >= end:
            raise InvalidDateRangeError("Start must be earlier than end.")

    @staticmethod
    def _missing_ranges(
        start: datetime,
        end: datetime,
        interval: BarInterval,
        coverage: Sequence[CoverageRange],
    ) -> list[tuple[datetime, datetime]]:
        step = INTERVAL_DURATION[interval]
        cursor = start
        missing: list[tuple[datetime, datetime]] = []
        for covered in sorted(coverage, key=lambda item: item.start):
            if covered.end < cursor:
                continue
            if covered.start > cursor:
                missing_end = min(end, covered.start - step)
                if cursor <= missing_end:
                    missing.append((cursor, missing_end))
            cursor = max(cursor, covered.end + step)
            if cursor > end:
                break
        if cursor <= end:
            missing.append((cursor, end))
        return missing

    def _recent_refresh_range(
        self, start: datetime, end: datetime, interval: BarInterval
    ) -> tuple[datetime, datetime] | None:
        step = INTERVAL_DURATION[interval]
        now = self._clock().astimezone(UTC)
        if end.astimezone(UTC) < now - step * self._recent_edge_bars:
            return None
        return max(start, end - step * self._recent_edge_bars), end

    @staticmethod
    def _merge_ranges(
        ranges: Sequence[tuple[datetime, datetime]],
    ) -> list[tuple[datetime, datetime]]:
        merged: list[tuple[datetime, datetime]] = []
        for start, end in sorted(ranges):
            if not merged or start > merged[-1][1]:
                merged.append((start, end))
            else:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        return merged

    def _chunk_range(
        self, start: datetime, end: datetime, interval: BarInterval
    ) -> list[tuple[datetime, datetime]]:
        step = INTERVAL_DURATION[interval]
        span = step * (self._max_points_per_chunk - 1)
        chunks: list[tuple[datetime, datetime]] = []
        cursor = start
        while cursor <= end:
            chunk_end = min(end, cursor + span)
            chunks.append((cursor, chunk_end))
            if chunk_end == end:
                break
            cursor = chunk_end + step
        return chunks
