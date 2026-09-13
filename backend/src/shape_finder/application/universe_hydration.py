import asyncio
import hashlib
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from shape_finder.application.market_data_service import MarketDataService
from shape_finder.application.universe import UniverseService
from shape_finder.core.errors import (
    AuthenticationError,
    CacheError,
    DailyQuotaError,
    InvalidSymbolError,
    MalformedProviderResponseError,
    MarketDataError,
    MissingApiKeyError,
    NoDataError,
    ProviderNetworkError,
    ProviderRejectedError,
    RateLimitError,
)
from shape_finder.core.market_data import BarInterval
from shape_finder.core.persistence import HydrationFailureRecord, MarketDataRepository
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
    fetched: int
    persisted: int
    became_ready: int
    suppressed: int
    candidates_considered: int
    candidates_skipped_historical_ineligible: int
    candidates_skipped_cooldown: int
    candidates_prioritized_partial_cache: int
    useful_success_rate: float
    failure_counts: dict[str, int]
    provider_rate_limited: bool
    provider_daily_quota: bool
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
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._market_data = market_data
        self._repository = repository
        self._universe_service = universe_service
        self._max_symbols = max_symbols
        self._intraday_max_symbols = min(intraday_max_symbols, max_symbols)
        self._timeout_seconds = timeout_seconds
        self._clock = clock
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
        now = self._clock().astimezone(UTC)
        suppressed_symbols = set(
            await self._repository.get_suppressed_hydration_symbols(
                missing, interval, start, end, now
            )
        )
        eligible_missing = tuple(symbol for symbol in missing if symbol not in suppressed_symbols)
        partial_symbols = set(
            await self._repository.get_partial_coverage_symbols(
                eligible_missing, interval, start, end
            )
        )
        limit = self._max_symbols if interval is BarInterval.ONE_DAY else self._intraday_max_symbols
        selected = self._select_batch(
            eligible_missing,
            self._last_attempted.get(key),
            limit,
            partial_symbols,
        )
        prioritized_partial = len(partial_symbols.intersection(selected))
        attempted = succeeded = failed = fetched = persisted = became_ready = 0
        failures: Counter[str] = Counter()
        rate_limited = daily_quota = provider_unavailable = timed_out = False
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
                failures["timeout"] += 1
                self._log_failure(symbol, interval, start, end, "timeout", None)
                timed_out = True
                break
            except DailyQuotaError as error:
                failed += 1
                failures["provider_daily_quota"] += 1
                self._log_failure(symbol, interval, start, end, "provider_daily_quota", error)
                daily_quota = True
                break
            except RateLimitError as error:
                failed += 1
                failures["provider_rate_limited"] += 1
                self._log_failure(symbol, interval, start, end, "provider_rate_limited", error)
                rate_limited = True
                break
            except (
                AuthenticationError,
                CacheError,
                MissingApiKeyError,
                ProviderNetworkError,
            ) as error:
                failed += 1
                category = (
                    "cache_error"
                    if isinstance(error, CacheError)
                    else (
                        "provider_rejected"
                        if isinstance(error, (AuthenticationError, MissingApiKeyError))
                        else "provider_unavailable"
                    )
                )
                failures[category] += 1
                self._log_failure(symbol, interval, start, end, category, error)
                provider_unavailable = True
                break
            except (InvalidSymbolError, NoDataError, ProviderRejectedError) as error:
                failed += 1
                category = self._category(error)
                failures[category] += 1
                self._log_failure(symbol, interval, start, end, category, error)
                await self._suppress(symbol, interval, start, end, category, now)
                continue
            except MalformedProviderResponseError as error:
                failed += 1
                failures["malformed_response"] += 1
                self._log_failure(symbol, interval, start, end, "malformed_response", error)
                continue
            except MarketDataError as error:
                failed += 1
                failures["unknown"] += 1
                self._log_failure(symbol, interval, start, end, "unknown", error)
                continue
            else:
                fetched += 1
                persisted += 1
                symbol_ready = bool(
                    await self._repository.get_scan_ready_symbols(
                        (symbol,), interval, start, end, reference_bar_count
                    )
                )
                if symbol_ready:
                    succeeded += 1
                    became_ready += 1
                    await self._repository.clear_hydration_failure(symbol, interval, start, end)
                    logger.info(
                        "hydration_candidate symbol=%s interval=%s start=%s end=%s "
                        "category=ready fetched=true persisted=true scan_ready=true",
                        symbol,
                        interval.value,
                        start.isoformat(),
                        end.isoformat(),
                    )
                else:
                    failed += 1
                    failures["insufficient_coverage"] += 1
                    self._log_failure(symbol, interval, start, end, "insufficient_coverage", None)
                    await self._suppress(
                        symbol,
                        interval,
                        start,
                        end,
                        "insufficient_coverage",
                        now,
                    )

        ready_after = tuple(
            await self._repository.get_scan_ready_symbols(
                symbols, interval, start, end, reference_bar_count
            )
        )
        logger.info(
            "universe_hydration universe=%s ready_before=%s attempted=%s succeeded=%s "
            "failed=%s fetched=%s persisted=%s became_ready=%s suppressed=%s "
            "failure_counts=%s ready_after=%s rate_limited=%s daily_quota=%s "
            "provider_unavailable=%s timed_out=%s candidates_considered=%s "
            "skipped_historical_ineligible=0 skipped_cooldown=%s "
            "prioritized_partial=%s useful_success_rate=%.3f",
            kind.value,
            len(ready_before),
            attempted,
            succeeded,
            failed,
            fetched,
            persisted,
            became_ready,
            len(suppressed_symbols),
            dict(sorted(failures.items())),
            len(ready_after),
            rate_limited,
            daily_quota,
            provider_unavailable,
            timed_out,
            len(missing),
            len(suppressed_symbols),
            prioritized_partial,
            became_ready / attempted if attempted else 0.0,
        )
        return UniverseHydrationResult(
            symbols=symbols,
            ready_before=ready_before,
            ready_after=ready_after,
            hydration_limit=limit,
            attempted=attempted,
            succeeded=succeeded,
            failed=failed,
            fetched=fetched,
            persisted=persisted,
            became_ready=became_ready,
            suppressed=len(suppressed_symbols),
            candidates_considered=len(missing),
            candidates_skipped_historical_ineligible=0,
            candidates_skipped_cooldown=len(suppressed_symbols),
            candidates_prioritized_partial_cache=prioritized_partial,
            useful_success_rate=became_ready / attempted if attempted else 0.0,
            failure_counts=dict(sorted(failures.items())),
            provider_rate_limited=rate_limited,
            provider_daily_quota=daily_quota,
            provider_unavailable=provider_unavailable,
            timed_out=timed_out,
            universe_stale=stale,
        )

    @staticmethod
    def _select_batch(
        missing: tuple[str, ...],
        last_attempted: str | None,
        limit: int,
        partial_symbols: set[str] | None = None,
    ) -> tuple[str, ...]:
        if not missing:
            return ()
        partial = partial_symbols or set()
        ordered = tuple(sorted((s for s in missing if s in partial), key=_stable_priority)) + tuple(
            sorted((s for s in missing if s not in partial), key=_stable_priority)
        )
        if last_attempted is None or last_attempted not in ordered:
            return ordered[:limit]
        cursor = ordered.index(last_attempted) + 1
        return (ordered[cursor:] + ordered[:cursor])[:limit]

    @staticmethod
    def _category(error: MarketDataError) -> str:
        if isinstance(error, NoDataError):
            return "no_data"
        if isinstance(error, InvalidSymbolError):
            return "unsupported_symbol"
        return "provider_rejected"

    async def _suppress(
        self,
        symbol: str,
        interval: BarInterval,
        start: datetime,
        end: datetime,
        category: str,
        now: datetime,
    ) -> None:
        cooldown = timedelta(days=1 if category == "insufficient_coverage" else 7)
        await self._repository.record_hydration_failure(
            HydrationFailureRecord(
                symbol=symbol,
                interval=interval,
                start=start,
                end=end,
                category=category,
                failed_at=now,
                retry_after=now + cooldown,
            )
        )

    @staticmethod
    def _log_failure(
        symbol: str,
        interval: BarInterval,
        start: datetime,
        end: datetime,
        category: str,
        error: Exception | None,
    ) -> None:
        logger.warning(
            "hydration_candidate symbol=%s interval=%s start=%s end=%s category=%s "
            "provider_status=%s provider_code=%s",
            symbol,
            interval.value,
            start.isoformat(),
            end.isoformat(),
            category,
            getattr(error, "provider_status", None),
            getattr(error, "provider_code", None),
        )


def _stable_priority(symbol: str) -> tuple[bytes, str]:
    return hashlib.sha256(symbol.encode("ascii")).digest(), symbol
