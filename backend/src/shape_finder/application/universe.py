import asyncio
import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from shape_finder.core.errors import MarketDataError
from shape_finder.core.universe import (
    SymbolMetadata,
    UniverseDescriptor,
    UniverseKind,
    UniverseProvider,
    UniverseRepository,
)

UNIVERSE_NAMES = {
    UniverseKind.US_EQUITIES: "U.S. stocks",
    UniverseKind.NASDAQ: "NASDAQ",
    UniverseKind.NYSE: "NYSE",
}
_US_COUNTRIES = {"US", "USA", "UNITED STATES", "UNITED STATES OF AMERICA"}
logger = logging.getLogger("shape_finder.universe")


class UniverseService:
    """Refresh, normalize, cache, and resolve provider-independent stock universes."""

    def __init__(
        self,
        provider: UniverseProvider,
        repository: UniverseRepository,
        *,
        ttl: timedelta = timedelta(hours=24),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._ttl = ttl
        self._clock = clock
        self._refresh_lock = asyncio.Lock()

    async def list_universes(self) -> tuple[UniverseDescriptor, ...]:
        symbols, refreshed_at, stale = await self._catalog()
        return tuple(
            UniverseDescriptor(
                id=kind,
                name=UNIVERSE_NAMES[kind],
                total_symbols=len(_select(symbols, kind)),
                refreshed_at=refreshed_at,
                stale=stale,
            )
            for kind in (
                UniverseKind.US_EQUITIES,
                UniverseKind.NASDAQ,
                UniverseKind.NYSE,
            )
        )

    async def resolve(self, kind: UniverseKind) -> tuple[tuple[str, ...], datetime | None, bool]:
        if kind is UniverseKind.CUSTOM:
            return (), None, False
        symbols, refreshed_at, stale = await self._catalog()
        selected = _select(symbols, kind)
        return tuple(item.symbol for item in selected), refreshed_at, stale

    async def _catalog(
        self,
    ) -> tuple[tuple[SymbolMetadata, ...], datetime | None, bool]:
        async with self._refresh_lock:
            return await self._catalog_locked()

    async def _catalog_locked(
        self,
    ) -> tuple[tuple[SymbolMetadata, ...], datetime | None, bool]:
        cached = tuple(await self._repository.list_universe_symbols())
        refreshed_at = await self._repository.get_universe_refreshed_at()
        now = self._clock()
        fresh = refreshed_at is not None and now - refreshed_at <= self._ttl
        if cached and fresh:
            logger.info("universe_cache_hit symbols=%s", len(cached))
            return cached, refreshed_at, False

        try:
            normalized = _normalize_catalog(await self._provider.list_stocks())
            await self._repository.replace_universe(
                normalized, refreshed_at=now, source="twelve_data"
            )
            logger.info("universe_refresh_complete symbols=%s", len(normalized))
            return normalized, now, False
        except MarketDataError:
            if cached:
                logger.warning("universe_refresh_failed using_stale_cache=true")
                return cached, refreshed_at, True
            raise


def _normalize_catalog(symbols: Sequence[SymbolMetadata]) -> tuple[SymbolMetadata, ...]:
    normalized: dict[str, SymbolMetadata] = {}
    for item in symbols:
        symbol = item.symbol.strip().upper()
        country = item.country.strip()
        security_type = item.security_type.strip()
        if (
            not symbol
            or country.upper() not in _US_COUNTRIES
            or security_type.casefold() != "common stock"
            or not item.active
        ):
            continue
        candidate = SymbolMetadata(
            symbol=symbol,
            name=item.name.strip(),
            exchange=item.exchange.strip().upper(),
            country="United States",
            security_type="Common Stock",
            currency=item.currency.strip().upper(),
            active=True,
        )
        current = normalized.get(symbol)
        if current is None or _exchange_priority(candidate.exchange) < _exchange_priority(
            current.exchange
        ):
            normalized[symbol] = candidate
    return tuple(normalized[symbol] for symbol in sorted(normalized))


def _select(symbols: Sequence[SymbolMetadata], kind: UniverseKind) -> tuple[SymbolMetadata, ...]:
    if kind is UniverseKind.US_EQUITIES:
        return tuple(symbols)
    exchange = "NASDAQ" if kind is UniverseKind.NASDAQ else "NYSE"
    return tuple(item for item in symbols if item.exchange == exchange)


def _exchange_priority(exchange: str) -> tuple[int, str]:
    priorities = {"NASDAQ": 0, "NYSE": 1}
    return priorities.get(exchange, 2), exchange
