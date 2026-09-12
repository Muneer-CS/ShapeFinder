from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from shape_finder.core.market_data import BarInterval


class UniverseKind(StrEnum):
    CUSTOM = "custom"
    US_EQUITIES = "us_equities"
    NASDAQ = "nasdaq"
    NYSE = "nyse"


@dataclass(frozen=True, slots=True)
class SymbolMetadata:
    symbol: str
    name: str
    exchange: str
    country: str
    security_type: str
    currency: str
    active: bool = True


@dataclass(frozen=True, slots=True)
class UniverseSelection:
    kind: UniverseKind
    symbols: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UniverseDescriptor:
    id: UniverseKind
    name: str
    total_symbols: int
    refreshed_at: datetime | None
    stale: bool


class UniverseProvider(Protocol):
    async def list_stocks(self) -> Sequence[SymbolMetadata]: ...


class UniverseRepository(Protocol):
    async def replace_universe(
        self, symbols: Sequence[SymbolMetadata], *, refreshed_at: datetime, source: str
    ) -> None: ...

    async def list_universe_symbols(self) -> Sequence[SymbolMetadata]: ...

    async def get_universe_refreshed_at(self) -> datetime | None: ...

    async def get_scan_ready_symbols(
        self,
        symbols: Sequence[str],
        interval: BarInterval,
        start: datetime,
        end: datetime,
        minimum_bars: int,
    ) -> Sequence[str]: ...
