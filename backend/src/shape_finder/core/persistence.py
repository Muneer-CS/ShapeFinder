from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from shape_finder.core.market_data import BarInterval, TimeSeries


@dataclass(frozen=True, slots=True)
class CoverageRange:
    start: datetime
    end: datetime
    synced_at: datetime


class MarketDataRepository(Protocol):
    async def initialize(self) -> None: ...

    async def get_time_series(
        self, symbol: str, interval: BarInterval, start: datetime, end: datetime
    ) -> TimeSeries: ...

    async def get_coverage(
        self, symbol: str, interval: BarInterval, start: datetime, end: datetime
    ) -> Sequence[CoverageRange]: ...

    async def upsert_time_series(
        self,
        series: Sequence[TimeSeries],
        coverage: Sequence[CoverageRange],
        *,
        source: str,
    ) -> None: ...

    async def get_scan_ready_symbols(
        self,
        symbols: Sequence[str],
        interval: BarInterval,
        start: datetime,
        end: datetime,
        minimum_bars: int,
    ) -> Sequence[str]: ...


@dataclass(frozen=True, slots=True)
class SimilarityResult:
    reference_symbol: str
    candidate_symbol: str
    candidate_start: datetime
    candidate_end: datetime
    score: float


class SimilarityResultRepository(Protocol):
    """Storage boundary, independent of SQLite or a future PostgreSQL adapter."""

    async def save(self, result: SimilarityResult) -> None: ...
