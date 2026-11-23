"""Plan and read Massive daily history, without network I/O in the scan path."""

import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from importlib.metadata import version

from shape_finder.application.current_pattern_scan import (
    CurrentPatternScanner,
    CurrentScanEvaluation,
    EligibleHistory,
    ExcludedHistory,
    ScanControl,
    hypotheses,
    match_rank_key,
)
from shape_finder.core.candle_eligibility import CandleIdentity, Freshness, FrozenCutoff
from shape_finder.core.current_data import DataUnavailable
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.pattern_scan import (
    CanonicalPattern,
    Coverage,
    CurrentPatternMatch,
    CurrentScanSettings,
    ExecutionStatus,
)
from shape_finder.core.universe import SymbolMetadata
from shape_finder.infrastructure.exchange_calendar import ListingCalendar
from shape_finder.infrastructure.market_data.massive import EASTERN, MassiveClient
from shape_finder.infrastructure.persistence.massive_store import (
    HISTORY_BATCH_SIZE,
    DailyHistory,
    MassiveStore,
)

SCHEDULE_LISTING = SymbolMetadata("AAPL", "Apple", "NASDAQ", "United States", "Common Stock", "USD")
MAX_SESSION_LAG = 2


def publication_at(candle: CandleIdentity, publication: time) -> datetime:
    release = datetime.combine(candle.session_date, publication, EASTERN).astimezone(UTC)
    return max(release, candle.close_at)


def required_sessions(
    cutoff: datetime,
    count: int,
    publication: time,
    listing: SymbolMetadata = SCHEDULE_LISTING,
) -> tuple[CandleIdentity, ...]:
    day = cutoff.astimezone(EASTERN).date()
    calendar = ListingCalendar(
        listing, max(date(2000, 1, 1), day - timedelta(days=count * 2 + 30)), day
    )
    eligible = tuple(
        candle
        for candle in calendar.candles(BarInterval.ONE_DAY)
        if publication_at(candle, publication) <= cutoff
    )[-count:]
    if len(eligible) != count:
        raise DataUnavailable("calendar_history_insufficient")
    return eligible


def cached_history(
    store: MassiveStore,
    symbol: str,
    required: tuple[CandleIdentity, ...],
    cutoff: FrozenCutoff,
    publication: time,
    calendar_version: str,
) -> EligibleHistory:
    cached = store.history(symbol, required[0].session_date, required[-1].session_date)
    return _select_history(
        symbol,
        required,
        cached,
        cutoff,
        {c.session_date: publication_at(c, publication) for c in required},
        calendar_version,
        minimum=len(required),
        maximum=len(required),
        max_lag=0,
    )


def _select_history(
    symbol: str,
    schedule: tuple[CandleIdentity, ...],
    cached: DailyHistory,
    cutoff: FrozenCutoff,
    published: Mapping[date, datetime],
    calendar_version: str,
    *,
    minimum: int,
    maximum: int,
    max_lag: int,
) -> EligibleHistory:
    end = None
    for index in range(len(schedule) - 1, -1, -1):
        day = schedule[index].session_date
        if day in cached and cached[day][1] >= published[day]:
            end = index
            break
    if end is None:
        raise DataUnavailable("missing_expected_candle")
    if len(schedule) - 1 - end > max_lag:
        raise DataUnavailable(
            "massive_daily_stale_candle" if max_lag else "missing_expected_candle"
        )
    # Only a contiguous suffix can be scored. A gap can shorten the window
    # within a duration range, but can never move its endpoint backward.
    start = end
    for index in range(end - 1, max(-1, end - maximum), -1):
        day = schedule[index].session_date
        if day not in cached or cached[day][1] < published[day]:
            break
        start = index
    required = schedule[start : end + 1]
    if len(required) < minimum:
        raise DataUnavailable("massive_daily_history_gap")
    expected = required[-1]
    bars = tuple(
        PriceBar(candle.open_at, *cached[candle.session_date][0][1:]) for candle in required
    )
    eligible_at = published[expected.session_date]
    return EligibleHistory(
        symbol,
        TimeSeries(symbol, BarInterval.ONE_DAY, expected.timezone, bars),
        Freshness(
            cutoff_at=cutoff.cutoff_at,
            status="current",
            expected=expected,
            retrieved_at=cached[expected.session_date][1],
            is_complete=True,
            publishable_at=eligible_at,
            finalizable_at=eligible_at,
            calendar_version=calendar_version,
        ),
        required,
    )


def scan_cached_daily(
    store: MassiveStore,
    pattern: CanonicalPattern,
    settings: CurrentScanSettings,
    cutoff: FrozenCutoff,
    members: Sequence[SymbolMetadata],
    publication: time,
    control: ScanControl,
    max_hypotheses: int,
) -> tuple[CurrentScanEvaluation, dict[str, date]]:
    maximum = settings.duration.maximum_candles
    # Complete durations increase in order, so the first possible hypothesis
    # also has the shortest observed suffix for this mode and completion floor.
    minimum = next(hypotheses(settings, maximum)).observed
    exclusions: list[ExcludedHistory] = []
    matches: list[CurrentPatternMatch] = []
    evaluated: dict[str, date] = {}
    eligible = incomplete = work = 0
    symbols = sorted(member.symbol for member in members)
    schedule: tuple[CandleIdentity, ...] = ()
    stop = control.stop_reason()
    if stop is None:
        try:
            schedule = required_sessions(
                cutoff.cutoff_at, maximum + MAX_SESSION_LAG, publication, members[0]
            )
        except DataUnavailable as error:
            exclusions.extend(ExcludedHistory(s, "skipped", str(error)) for s in symbols)
    if schedule:
        calendar_version = (
            f"exchange_calendars:{version('exchange_calendars')}:{schedule[-1].calendar_id}"
        )
        published = {c.session_date: publication_at(c, publication) for c in schedule}
        for offset in range(0, len(symbols), HISTORY_BATCH_SIZE):
            stop = stop or control.stop_reason()
            if stop is not None:
                exclusions.extend(
                    ExcludedHistory(s, "not_evaluated", stop) for s in symbols[offset:]
                )
                break
            batch = symbols[offset : offset + HISTORY_BATCH_SIZE]
            try:
                rows = store.histories(batch, schedule[0].session_date, schedule[-1].session_date)
            except sqlite3.Error:
                exclusions.extend(
                    ExcludedHistory(s, "failed", "massive_cache_read_error") for s in batch
                )
                continue
            histories = []
            for symbol in batch:
                stop = control.stop_reason()
                if stop is not None:
                    exclusions.append(ExcludedHistory(symbol, "not_evaluated", stop))
                    continue
                cached = rows[symbol]
                if cached is None:
                    exclusions.append(ExcludedHistory(symbol, "failed", "malformed_history"))
                    continue
                try:
                    histories.append(
                        _select_history(
                            symbol,
                            schedule,
                            cached,
                            cutoff,
                            published,
                            calendar_version,
                            minimum=minimum,
                            maximum=maximum,
                            max_lag=MAX_SESSION_LAG,
                        )
                    )
                except DataUnavailable as error:
                    if str(error) == "massive_daily_stale_candle":
                        exclusions.append(ExcludedHistory(symbol, "stale", str(error)))
                    else:
                        exclusions.append(ExcludedHistory(symbol, "skipped", str(error)))
                except (TypeError, ValueError):
                    exclusions.append(ExcludedHistory(symbol, "failed", "malformed_history"))
            result = CurrentPatternScanner(max_hypotheses=max_hypotheses - work).scan(
                pattern,
                settings,
                cutoff,
                histories,
                control=control,
                recent_daily=True,
            )
            excluded_symbols = {item.symbol for item in result.exclusions}
            evaluated.update(
                (history.symbol, history.required_candles[-1].session_date)
                for history in histories
                if history.symbol not in excluded_symbols
            )
            exclusions.extend(result.exclusions)
            eligible += result.coverage.eligible
            incomplete += result.coverage.evaluation_incomplete
            work += result.execution.hypotheses_evaluated
            for reason in result.execution.stop_reasons:
                if reason == "computation_budget":
                    stop = "computation_budget"
                elif reason == "deadline":
                    stop = "deadline"
                elif reason == "cancelled":
                    stop = "cancelled"
            matches = sorted((*matches, *result.matches), key=match_rank_key)[: settings.top_n]
    elif stop is not None:
        exclusions.extend(ExcludedHistory(s, "not_evaluated", stop) for s in symbols)
    counts = Counter(item.category for item in exclusions)
    coverage = Coverage(
        universe_total=len(symbols),
        scanned=len(evaluated),
        eligible=eligible,
        stale=counts["stale"],
        skipped=counts["skipped"],
        failed=counts["failed"],
        not_evaluated=counts["not_evaluated"],
        evaluation_incomplete=incomplete,
        matches_returned=len(matches),
    )
    execution = ExecutionStatus(
        partial=coverage.scanned < coverage.universe_total,
        stop_reasons=(stop,) if stop else (),
        hypotheses_evaluated=work,
    )
    return CurrentScanEvaluation(tuple(matches), coverage, execution, tuple(exclusions)), evaluated


def refresh_daily(
    store: MassiveStore,
    client: MassiveClient,
    cutoff: datetime,
    count: int,
    publication: time,
    catalogue_ttl: timedelta,
    *,
    force_catalogue: bool = False,
) -> dict[str, object]:
    required = required_sessions(cutoff, count, publication)
    members, refreshed = store.catalog_snapshot()
    if force_catalogue or refreshed is None or cutoff - refreshed >= catalogue_ttl:
        members = client.catalogue()
        store.replace_catalog(members, datetime.fromtimestamp(client.clock(), UTC))
    completed = store.completed()
    member_symbols = {member.symbol for member in members}
    gaps: dict[str, int] = {}
    failures: dict[str, str] = {}
    fetched = 0
    for candle in required:
        day = candle.session_date
        eligible_at = publication_at(candle, publication)
        if day in completed and completed[day] >= eligible_at:
            continue
        try:
            bars = client.grouped(day)
            if member_symbols.isdisjoint(b.symbol for b in bars):
                raise DataUnavailable("massive_no_listings_in_session")
            # Retain the whole validated batch. Reads join against the active
            # catalogue, including new listings added by a later refresh.
            store.save_session(day, bars, datetime.fromtimestamp(client.clock(), UTC), eligible_at)
        except DataUnavailable as error:
            failures[day.isoformat()] = str(error)
            if str(error) in {
                "massive_http_401",
                "massive_http_403",
                "massive_rate_limited",
                "provider_cooldown",
                "provider_busy",
            }:
                break
            continue
        gaps[day.isoformat()] = len(member_symbols.difference(b.symbol for b in bars))
        fetched += 1
    completed = store.completed()
    return {
        "first_session": required[0].session_date.isoformat(),
        "expected_session": required[-1].session_date.isoformat(),
        "catalogue_size": len(members),
        "requested_sessions": count,
        "fetched_sessions": fetched,
        "pending_sessions": [
            c.session_date.isoformat()
            for c in required
            if c.session_date not in completed
            or completed[c.session_date] < publication_at(c, publication)
        ],
        "missing_listings_in_fetched_batches": gaps,
        "failures": failures,
        "http_attempts": client.attempts,
    }
