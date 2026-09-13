import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from shape_finder.application.market_data_service import MarketDataService
from shape_finder.application.universe import UniverseService
from shape_finder.core.errors import (
    AuthenticationError,
    InvalidSymbolError,
    MalformedProviderResponseError,
    MarketDataError,
    MissingApiKeyError,
    NoDataError,
    ProviderNetworkError,
    RateLimitError,
)
from shape_finder.core.market_data import BarInterval
from shape_finder.core.persistence import MarketDataRepository
from shape_finder.core.universe import UniverseKind

logger = logging.getLogger("shape_finder.hydration")


@dataclass(frozen=True, slots=True)
class UniverseHydrationResult:
    symbols: tuple[str, ...]
    ready_before: tuple[str, ...]
    ready_after: tuple[str, ...]
    hydration_limit: int
    attempted: int
    succeeded: int
    failed: int
    provider_rate_limited: bool
    provider_unavailable: bool
    timed_out: bool
    universe_stale: bool


class UniverseHydrationService:
    """Progressively make a bounded slice of a named universe scan-ready."""

    def __init__(
        self,
        market_data: MarketDataService,
        repository: MarketDataRepository,
        universe_service: UniverseService,
        *,
        max_symbols: int = 5,
        intraday_max_symbols: int = 2,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._market_data = market_data
        self._repository = repository
        self._universe_service = universe_service
        self._max_symbols = max_symbols
        self._intraday_max_symbols = min(intraday_max_symbols, max_symbols)
        self._timeout_seconds = timeout_seconds
        self._locks: dict[tuple[object, ...], asyncio.Lock] = {}
        self._last_attempted: dict[tuple[object, ...], str] = {}

    async def hydrate(
        self,
        kind: UniverseKind,
        start: datetime,
        end: datetime,
        interval: BarInterval,
        reference_bar_count: int,
    ) -> UniverseHydrationResult:
        key = (kind, start, end, interval, reference_bar_count)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            return await self._hydrate_locked(key, kind, start, end, interval, reference_bar_count)

    async def _hydrate_locked(
        self,
        key: tuple[object, ...],
        kind: UniverseKind,
        start: datetime,
        end: datetime,
        interval: BarInterval,
        reference_bar_count: int,
    ) -> UniverseHydrationResult:
        symbols, _, stale = await self._universe_service.resolve(kind)
        ready_before = tuple(
            await self._repository.get_scan_ready_symbols(
                symbols, interval, start, end, reference_bar_count
            )
        )
        ready_set = set(ready_before)
        missing = tuple(symbol for symbol in sorted(symbols) if symbol not in ready_set)
        limit = self._max_symbols if interval is BarInterval.ONE_DAY else self._intraday_max_symbols
        selected = self._select_batch(missing, self._last_attempted.get(key), limit)
        attempted = succeeded = failed = 0
        rate_limited = provider_unavailable = timed_out = False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds

        for symbol in selected:
            remaining = deadline - loop.time()
            if remaining <= 0:
                timed_out = True
                break
            attempted += 1
            self._last_attempted[key] = symbol
            try:
                await asyncio.wait_for(
                    self._market_data.get_time_series(symbol, start, end, interval),
                    timeout=remaining,
                )
            except TimeoutError:
                failed += 1
                timed_out = True
                break
            except RateLimitError:
                failed += 1
                rate_limited = True
                break
            except (
                AuthenticationError,
                MissingApiKeyError,
                ProviderNetworkError,
            ):
                failed += 1
                provider_unavailable = True
                break
            except (InvalidSymbolError, NoDataError, MalformedProviderResponseError):
                failed += 1
                continue
            except MarketDataError:
                failed += 1
                continue
            else:
                succeeded += 1

        ready_after = tuple(
            await self._repository.get_scan_ready_symbols(
                symbols, interval, start, end, reference_bar_count
            )
        )
        logger.info(
            "universe_hydration universe=%s ready_before=%s attempted=%s succeeded=%s "
            "failed=%s ready_after=%s rate_limited=%s provider_unavailable=%s timed_out=%s",
            kind.value,
            len(ready_before),
            attempted,
            succeeded,
            failed,
            len(ready_after),
            rate_limited,
            provider_unavailable,
            timed_out,
        )
        return UniverseHydrationResult(
            symbols=symbols,
            ready_before=ready_before,
            ready_after=ready_after,
            hydration_limit=limit,
            attempted=attempted,
            succeeded=succeeded,
            failed=failed,
            provider_rate_limited=rate_limited,
            provider_unavailable=provider_unavailable,
            timed_out=timed_out,
            universe_stale=stale,
        )

    @staticmethod
    def _select_batch(
        missing: tuple[str, ...], last_attempted: str | None, limit: int
    ) -> tuple[str, ...]:
        if not missing:
            return ()
        if last_attempted is None:
            return missing[:limit]
        later = tuple(symbol for symbol in missing if symbol > last_attempted)
        earlier = tuple(symbol for symbol in missing if symbol <= last_attempted)
        return (later + earlier)[:limit]
