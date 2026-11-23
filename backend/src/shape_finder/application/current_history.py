"""Canonicalization, exact readiness and bounded chunk loading for stage 6B consumers."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from shape_finder.application.current_pattern_scan import EligibleHistory
from shape_finder.core.candle_eligibility import (
    CachedCandle,
    CandleIdentity,
    Clock,
    FrozenCutoff,
    SystemClock,
    next_session_publications,
    resolve_eligibility,
)
from shape_finder.core.current_data import (
    CanonicalBar,
    CapabilityReport,
    DataUnavailable,
)
from shape_finder.core.errors import InvalidSymbolError, NoDataError
from shape_finder.core.market_data import BarInterval, TimeSeries, validate_time_series
from shape_finder.core.persistence import HydrationFailureRecord
from shape_finder.infrastructure.exchange_calendar import ListingCalendar
from shape_finder.infrastructure.market_data.current_dispatch import CurrentDataGateway
from shape_finder.infrastructure.persistence.current_data_store import CurrentDataStore


@dataclass(frozen=True)
class HistoryPlan:
    symbol: str
    calendar: ListingCalendar
    report: CapabilityReport
    cutoff: FrozenCutoff
    required: tuple[CandleIdentity, ...]

    @property
    def policy_key(self) -> str:
        return self.report.policy_key(self.calendar.version)

    def observation_thresholds(self) -> dict[datetime, datetime]:
        if self.report.capability.next_trading_day_publication:
            schedule = next_session_publications(
                self.calendar.sessions_through(self.cutoff.cutoff_at), self.calendar.timezone
            )
            return {c.open_at: schedule[c.session_date] for c in self.required}
        seconds = self.report.capability.finalization_delay_seconds
        if seconds is None:
            raise DataUnavailable("finalization_unknown")
        return {c.open_at: c.close_at + timedelta(seconds=seconds) for c in self.required}


def plan_history(
    symbol: str,
    calendar: ListingCalendar,
    report: CapabilityReport,
    cutoff: FrozenCutoff,
    maximum_candles: int,
    *,
    accept_configured_policy: bool = False,
) -> HistoryPlan:
    if not 8 <= maximum_candles <= 240:
        raise ValueError("Require 8..240 candles.")
    if symbol != calendar.symbol:
        raise DataUnavailable("listing_calendar_symbol_mismatch")
    if report.status == "unavailable":
        raise DataUnavailable(report.reason)
    if report.status == "configured_but_unverified" and not accept_configured_policy:
        raise DataUnavailable("configured_policy_requires_explicit_acceptance")
    pending = resolve_eligibility(cutoff, calendar, report.capability, None)
    if pending.expected is None:
        raise DataUnavailable(pending.reason.value if pending.reason else "expected_candle_unknown")
    if pending.finalizable_at is None or pending.finalizable_at > cutoff.cutoff_at:
        raise DataUnavailable("latest_candle_provisional")
    required = tuple(
        c
        for c in calendar.candles(report.capability.interval)
        if c.open_at <= pending.expected.open_at
    )[-maximum_candles:]
    if len(required) != maximum_candles or required[-1] != pending.expected:
        raise DataUnavailable("calendar_history_insufficient")
    return HistoryPlan(symbol, calendar, report, cutoff, required)


def canonicalize(
    source: TimeSeries,
    required: Sequence[CandleIdentity],
    plan: HistoryPlan,
    retrieved_at: datetime,
) -> tuple[CanonicalBar, ...]:
    """Validate response observations; missing requested observations remain missing.

    This function never claims coverage of a requested range, nor resamples OHLC.
    """
    validate_time_series(source)
    if source.symbol != plan.symbol or source.interval != plan.report.capability.interval:
        raise DataUnavailable("response_identity_mismatch")
    if any(a.timestamp >= b.timestamp for a, b in zip(source.bars, source.bars[1:], strict=False)):
        raise DataUnavailable("response_unordered")
    daily = source.interval is BarInterval.ONE_DAY
    if daily and source.timezone != plan.calendar.timezone:
        raise DataUnavailable("daily_exchange_timezone_mismatch")
    by_date = {c.session_date: c for c in required}
    by_open = {c.open_at: c for c in required}
    rows: list[CanonicalBar] = []
    seen: set[datetime] = set()
    for bar in source.bars:
        if daily:
            local = bar.timestamp.astimezone(ZoneInfo(plan.calendar.timezone))
            if local.time() != time():
                raise DataUnavailable("daily_session_date_semantics_mismatch")
            identity = by_date.get(local.date())
        else:
            identity = by_open.get(bar.timestamp)
        if identity is None or identity.open_at in seen:
            raise DataUnavailable("unexpected_or_duplicate_candle")
        seen.add(identity.open_at)
        rows.append(
            CanonicalBar(
                identity=identity,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                retrieved_at=retrieved_at,
                policy_key=plan.policy_key,
                calendar_version=plan.calendar.version,
            )
        )
    return tuple(rows)


def assess_history(plan: HistoryPlan, rows: Sequence[CanonicalBar]) -> EligibleHistory:
    if any(a.identity.open_at >= b.identity.open_at for a, b in zip(rows, rows[1:], strict=False)):
        raise DataUnavailable("cache_unordered_or_duplicate")
    by_open = {row.identity.open_at: row for row in rows}
    selected = [by_open.get(c.open_at) for c in plan.required]
    if any(row is None for row in selected):
        raise DataUnavailable("missing_required_candles")
    checked = tuple(row for row in selected if row is not None)
    thresholds = plan.observation_thresholds()
    for row, identity in zip(checked, plan.required, strict=True):
        if (
            row.identity != identity
            or row.policy_key != plan.policy_key
            or row.calendar_version != plan.calendar.version
        ):
            raise DataUnavailable("cache_provenance_mismatch")
        if row.retrieved_at < thresholds[identity.open_at]:
            raise DataUnavailable("cache_finalization_unproven")
    last = checked[-1]
    freshness = resolve_eligibility(
        plan.cutoff,
        plan.calendar,
        plan.report.capability,
        CachedCandle(identity=last.identity, retrieved_at=last.retrieved_at, finalized=True),
    )
    if freshness.status != "current":
        raise DataUnavailable(freshness.reason.value if freshness.reason else "freshness_unknown")
    series = TimeSeries(
        plan.symbol,
        last.identity.interval,
        plan.calendar.timezone,
        tuple(row.price_bar() for row in checked),
    )
    return EligibleHistory(plan.symbol, series, freshness, plan.required)


class CurrentHistoryDataService:
    def __init__(
        self,
        store: CurrentDataStore,
        gateway: CurrentDataGateway,
        clock: Clock | None = None,
        *,
        chunk_size: int = 240,
    ) -> None:
        if not 1 <= chunk_size <= 240:
            raise ValueError("Chunk size must lie in [1,240].")
        self.store, self.gateway, self.clock = store, gateway, clock or SystemClock()
        self.chunk_size = chunk_size  # Far below the documented 5000-point response maximum.

    async def load(self, plan: HistoryPlan) -> EligibleHistory:
        try:
            timeout = self.gateway.run.remaining()
            result = await asyncio.wait_for(self._load(plan), timeout=timeout)
        except TimeoutError as error:
            raise DataUnavailable("deadline") from error
        self.gateway.run.remaining()
        return result

    async def _load(self, plan: HistoryPlan) -> EligibleHistory:
        rows = await asyncio.to_thread(
            self.store.load, plan.symbol, plan.report.capability.interval, plan.policy_key
        )
        try:
            return assess_history(plan, rows)
        except DataUnavailable:
            pass
        first, last = plan.required[0].open_at, plan.required[-1].open_at
        suppressed = await self.store.repository.get_suppressed_hydration_symbols(
            [plan.symbol], plan.report.capability.interval, first, last, self.clock.now()
        )
        if suppressed:
            raise DataUnavailable("symbol_cooldown")
        thresholds = plan.observation_thresholds()
        usable = {
            row.identity.open_at
            for row in rows
            if row.policy_key == plan.policy_key
            and row.identity.open_at in thresholds
            and row.retrieved_at >= thresholds[row.identity.open_at]
        }
        missing = [c for c in plan.required if c.open_at not in usable]
        # Requests cover contiguous subsets of the REQUIRED sequence, not a guessed
        # weekday range or a vendor pagination token. There is no automatic retry of holes.
        for offset in range(0, len(missing), self.chunk_size):
            chunk = tuple(missing[offset : offset + self.chunk_size])
            # Include intervening cached candles if this date range will return them.
            expected = tuple(
                c for c in plan.required if chunk[0].open_at <= c.open_at <= chunk[-1].open_at
            )
            start, end = expected[0].open_at, expected[-1].open_at
            interval = plan.report.capability.interval
            if interval is BarInterval.ONE_DAY:
                exchange_tz = ZoneInfo(plan.calendar.timezone)
                start, end = start.astimezone(exchange_tz), end.astimezone(exchange_tz)
            try:
                source = await self.gateway.get_historical_bars(
                    plan.symbol,
                    start,
                    end,
                    interval,
                )
            except (InvalidSymbolError, NoDataError) as error:
                if (
                    isinstance(error, NoDataError)
                    and plan.report.capability.next_trading_day_publication
                    and plan.required[-1] in expected
                ):
                    raise DataUnavailable("missing_expected_candle") from error
                now = self.clock.now()
                await self.store.repository.record_hydration_failure(
                    HydrationFailureRecord(
                        plan.symbol,
                        plan.report.capability.interval,
                        first,
                        last,
                        "unsupported_symbol"
                        if isinstance(error, InvalidSymbolError)
                        else "no_data",
                        now,
                        now + timedelta(days=7),
                    )
                )  # Existing local hydration policy, not account timing.
                raise
            validated = canonicalize(source, expected, plan, self.clock.now())
            # Save proven individual observations even if the response is truncated.
            # A later chunk failure leaves earlier committed observations intact.
            await asyncio.to_thread(self.store.save, plan.symbol, validated)
            received = {row.identity for row in validated}
            if plan.required[-1] in expected and plan.required[-1] not in received:
                raise DataUnavailable("missing_expected_candle")
            if received != set(expected):
                raise DataUnavailable("truncated_or_missing_response")
        rows = await asyncio.to_thread(
            self.store.load, plan.symbol, plan.report.capability.interval, plan.policy_key
        )
        return assess_history(plan, rows)
