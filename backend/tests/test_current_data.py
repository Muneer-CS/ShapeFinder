import asyncio
import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import ValidationError

from shape_finder.application.current_history import (
    CurrentHistoryDataService,
    HistoryPlan,
    assess_history,
    canonicalize,
    plan_history,
)
from shape_finder.core.candle_eligibility import FrozenCutoff
from shape_finder.core.current_data import (
    AccountPolicy,
    DataUnavailable,
    DispatchLimits,
    TimingPolicy,
)
from shape_finder.core.errors import (
    MalformedProviderResponseError,
    NoDataError,
    ProviderNetworkError,
    RateLimitError,
)
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.universe import SymbolMetadata
from shape_finder.infrastructure.exchange_calendar import ListingCalendar
from shape_finder.infrastructure.market_data.current_capabilities import capability_report
from shape_finder.infrastructure.market_data.current_dispatch import (
    DispatchRun,
    SharedDispatcher,
    _CurrentProvider,
)
from shape_finder.infrastructure.persistence.current_data_store import CurrentDataStore
from shape_finder.infrastructure.persistence.migrations import MIGRATIONS


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def listing(exchange: str = "NASDAQ") -> SymbolMetadata:
    return SymbolMetadata("AAA", "Fixture", exchange, "United States", "Common Stock", "USD")


def timing(interval: str = "1day", **changes: Any) -> TimingPolicy:
    return TimingPolicy.model_validate(
        {
            "interval": interval,
            "publication_delay_seconds": 60,
            "finalization_delay_seconds": 120,
            "basis": "verified",
            "provenance": "Synthetic test policy only, not verified Twelve Data timing",
            "reviewed_at": "2025-07-16T21:00:00Z",
            **changes,
        }
    )


def plan() -> HistoryPlan:
    cal = ListingCalendar(listing(), date(2025, 6, 1), date(2025, 7, 16))
    return plan_history(
        "AAA",
        cal,
        capability_report(BarInterval.ONE_DAY, timing()),
        FrozenCutoff(cutoff_at=datetime(2025, 7, 16, 20, 3, tzinfo=UTC)),
        8,
    )


def source(p: HistoryPlan, identities: Any = None) -> TimeSeries:
    bars = tuple(
        PriceBar(
            datetime.combine(c.session_date, datetime.min.time(), ZoneInfo(p.calendar.timezone)),
            Decimal(100),
            Decimal(101),
            Decimal(99),
            Decimal(100),
            Decimal(1000),
        )
        for c in (identities or p.required)
    )
    return TimeSeries("AAA", BarInterval.ONE_DAY, p.calendar.timezone, bars)


@pytest.mark.parametrize(
    ("midnight", "previous_date", "published_date", "close_hour"),
    [
        ("2025-03-10T04:00:00+00:00", "2025-03-06", "2025-03-07", 21),
        ("2025-11-28T05:00:00+00:00", "2025-11-25", "2025-11-26", 21),
        ("2025-12-01T05:00:00+00:00", "2025-11-26", "2025-11-28", 18),
    ],
)
def test_next_trading_day_publication(
    midnight: str, previous_date: str, published_date: str, close_hour: int
) -> None:
    boundary = datetime.fromisoformat(midnight)
    day = boundary.date()
    cal = ListingCalendar(listing(), day - timedelta(days=30), day)
    report = capability_report(
        BarInterval.ONE_DAY,
        timing(
            basis="configured",
            next_trading_day_publication=True,
            publication_delay_seconds=None,
            finalization_delay_seconds=None,
        ),
    )
    assert not report.capability.unfinished_snapshots_verified
    for now, expected in [
        (boundary - timedelta(microseconds=1), previous_date),
        (boundary, published_date),
    ]:
        p = plan_history(
            "AAA", cal, report, FrozenCutoff(cutoff_at=now), 8, accept_configured_policy=True
        )
        assert p.required[-1].session_date.isoformat() == expected
    assert p.required[-1].close_at.hour == close_hour
    rows = canonicalize(source(p), p.required, p, boundary)
    assert assess_history(p, rows).freshness.publishable_at == boundary
    premature = rows[-1].model_copy(update={"retrieved_at": boundary - timedelta(seconds=1)})
    with pytest.raises(DataUnavailable, match="cache_finalization_unproven"):
        assess_history(p, (*rows[:-1], premature))


def payload(p: HistoryPlan, request: httpx.Request) -> dict[str, Any]:
    start, end = request.url.params["start_date"][:10], request.url.params["end_date"][:10]
    return {
        "meta": {"symbol": "AAA", "exchange_timezone": p.calendar.timezone},
        "values": [
            {
                "datetime": c.session_date.isoformat(),
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
                "volume": "1000",
            }
            for c in p.required
            if start <= c.session_date.isoformat() < end
        ],
    }


def store_at(tmp_path: Path) -> CurrentDataStore:
    s = CurrentDataStore(tmp_path / "data.sqlite3")
    s.initialize()
    return s


@pytest.mark.parametrize("exchange", ["NASDAQ", "NYSE", "XNAS", "XNYS"])
def test_real_calendar_listing_mapping_and_early_close(exchange: str) -> None:
    cal = ListingCalendar(listing(exchange), date(2025, 7, 1), date(2025, 7, 8))
    sessions = cal.sessions_through(datetime(2025, 7, 8, tzinfo=UTC))
    assert cal.calendar_id == "XNYS"
    assert "4.13.2" in cal.version
    assert date(2025, 7, 4) not in {s.session_date for s in sessions}
    early = next(s for s in sessions if s.session_date == date(2025, 7, 3))
    assert early.close_at == datetime(2025, 7, 3, 17, tzinfo=UTC)
    hours = [c for c in cal.candles(BarInterval.ONE_HOUR) if c.session_date == early.session_date]
    assert len(hours) == 4 and hours[-1].close_at - hours[-1].open_at == timedelta(minutes=30)


@pytest.mark.parametrize(
    "day,hour",
    [
        (date(2025, 3, 7), 14),
        (date(2025, 3, 10), 13),
        (date(2025, 10, 31), 13),
        (date(2025, 11, 3), 14),
    ],
)
def test_real_calendar_dst(day: date, hour: int) -> None:
    cal = ListingCalendar(listing(), day - timedelta(days=2), day + timedelta(days=1))
    item = next(c for c in cal.candles(BarInterval.ONE_DAY) if c.session_date == day)
    assert item.open_at.hour == hour and item.open_at.minute == 30


def test_unknown_mapping_window_and_capabilities_are_explicit() -> None:
    with pytest.raises(DataUnavailable, match="exchange_mapping"):
        ListingCalendar(listing("OTC"), date(2025, 1, 1), date(2025, 2, 1))
    with pytest.raises(DataUnavailable, match="supported_window"):
        ListingCalendar(listing(), date(1990, 1, 1), date(2025, 2, 1))
    assert capability_report(BarInterval.ONE_DAY).reason == "publication_finalization_unknown"
    assert (
        capability_report(BarInterval.ONE_HOUR, timing("1h")).reason == "bucket_alignment_unknown"
    )
    configured = capability_report(BarInterval.ONE_DAY, timing(basis="configured"))
    assert configured.status == "configured_but_unverified"
    p = plan()
    with pytest.raises(DataUnavailable, match="explicit_acceptance"):
        plan_history("AAA", p.calendar, configured, p.cutoff, 8)
    assert plan_history("AAA", p.calendar, configured, p.cutoff, 8, accept_configured_policy=True)
    assert not configured.capability.unfinished_snapshots_verified
    with pytest.raises(ValidationError):
        timing(finalization_delay_seconds=0)
    with pytest.raises(ValidationError):
        timing(session_open_buckets_confirmed=True)


def test_canonical_daily_session_dates_and_complete_readiness() -> None:
    p = plan()
    rows = canonicalize(source(p), p.required, p, p.cutoff.cutoff_at)
    assert rows[-1].identity.open_at.hour == 13
    h = assess_history(p, rows)
    assert h.series is not None and h.series.bars[-1].timestamp == p.required[-1].open_at
    assert h.freshness.status == "current"
    for broken, reason in [
        (rows[:-1], "missing_required"),
        (tuple(reversed(rows)), "unordered"),
        ((rows[0], *rows), "duplicate"),
    ]:
        with pytest.raises(DataUnavailable, match=reason):
            assess_history(p, broken)
    stale = rows[:-1] + (rows[-1].model_copy(update={"retrieved_at": p.required[-1].close_at}),)
    with pytest.raises(DataUnavailable, match="finalization_unproven"):
        assess_history(p, stale)


def test_reject_malformed_daily_and_unordered_provider_responses() -> None:
    p = plan()
    original = source(p)
    with pytest.raises(DataUnavailable, match="timezone"):
        canonicalize(replace(original, timezone="UTC"), p.required, p, p.cutoff.cutoff_at)
    with pytest.raises(DataUnavailable, match="unordered"):
        canonicalize(
            replace(original, bars=tuple(reversed(original.bars))),
            p.required,
            p,
            p.cutoff.cutoff_at,
        )
    request = httpx.Request(
        "GET",
        "https://api.twelvedata.com/time_series",
        params={"start_date": "2025-01-01", "end_date": "2025-12-31"},
    )
    body = payload(p, request)
    body["values"].reverse()
    with pytest.raises(MalformedProviderResponseError, match="chronologically"):
        _CurrentProvider._parse_time_series(body, BarInterval.ONE_DAY)


@pytest.mark.parametrize(
    ("interval", "start_day", "end_day", "expected_start", "expected_end"),
    [
        (BarInterval.ONE_DAY, 16, 16, "2025-07-16", "2025-07-17"),
        (BarInterval.ONE_DAY, 16, 18, "2025-07-16", "2025-07-19"),
        (
            BarInterval.ONE_HOUR,
            16,
            18,
            "2025-07-16T13:30:00",
            "2025-07-18T13:30:00",
        ),
    ],
)
def test_current_history_query_bounds(
    interval: BarInterval,
    start_day: int,
    end_day: int,
    expected_start: str,
    expected_end: str,
) -> None:
    tz = ZoneInfo("America/New_York")
    start = datetime(2025, 7, start_day, 9, 30, tzinfo=tz)
    end = datetime(2025, 7, end_day, 9, 30, tzinfo=tz)
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as client:
        params = _CurrentProvider(client, "test-key")._build_query(
            "AAA", start, end, interval, interval.value
        )
    assert (params["start_date"], params["end_date"]) == (expected_start, expected_end)


def test_intraday_mapping_never_rebuckets_ohlc() -> None:
    cal = ListingCalendar(listing(), date(2025, 7, 2), date(2025, 7, 3))
    report = capability_report(
        BarInterval.ONE_HOUR,
        timing(
            "1h",
            session_open_buckets_confirmed=True,
            bucket_provenance="Synthetic fixture bucket evidence",
        ),
    )
    p = plan_history(
        "AAA", cal, report, FrozenCutoff(cutoff_at=datetime(2025, 7, 3, 17, 3, tzinfo=UTC)), 8
    )
    raw = TimeSeries(
        "AAA",
        BarInterval.ONE_HOUR,
        "UTC",
        tuple(
            PriceBar(c.open_at, Decimal(100), Decimal(100), Decimal(100), Decimal(100), Decimal(1))
            for c in p.required
        ),
    )
    rows = canonicalize(raw, p.required, p, p.cutoff.cutoff_at)
    assert rows[-1].identity.close_at.hour == 17
    bad = replace(raw.bars[-1], timestamp=raw.bars[-1].timestamp + timedelta(minutes=1))
    with pytest.raises(DataUnavailable, match="unexpected"):
        canonicalize(replace(raw, bars=raw.bars[:-1] + (bad,)), p.required, p, p.cutoff.cutoff_at)


def test_migration_preserves_legacy_rows_without_promoting_proof(tmp_path: Path) -> None:
    path = tmp_path / "data.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at_utc TEXT)"
        )
        for migration in MIGRATIONS[:3]:
            for statement in migration.statements:
                db.execute(statement)
            db.execute(
                "INSERT INTO schema_migrations VALUES (?,?)", (migration.version, "2025-01-01")
            )
        db.execute(
            "INSERT INTO market_bars VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "AAA",
                "1day",
                "2025-07-16T04:00:00+00:00",
                "100",
                "101",
                "99",
                "100",
                "1",
                "America/New_York",
                "legacy",
                "2025-07-16T21:00:00+00:00",
            ),
        )
    store = store_at(tmp_path)
    store.initialize()
    assert store.load("AAA", BarInterval.ONE_DAY, plan().policy_key) == ()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM market_bars").fetchone()[0] == 1
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == len(
            MIGRATIONS
        )


def test_chunk_persistence_is_atomic_and_survives_later_failure(tmp_path: Path) -> None:
    p, store = plan(), store_at(tmp_path)
    rows = canonicalize(source(p), p.required, p, p.cutoff.cutoff_at)
    store.save("AAA", rows[:4])
    invalid = rows[4].model_copy(update={"close": Decimal("NaN")})
    with pytest.raises(ValidationError):
        store.save("AAA", [rows[4], invalid])
    assert len(store.load("AAA", BarInterval.ONE_DAY, p.policy_key)) == 4
    with pytest.raises(DataUnavailable, match="missing_required"):
        assess_history(p, store.load("AAA", BarInterval.ONE_DAY, p.policy_key))
    store.save("AAA", rows[4:])
    assert (
        assess_history(p, store.load("AAA", BarInterval.ONE_DAY, p.policy_key)).freshness.status
        == "current"
    )
    assert store.load("AAA", BarInterval.ONE_DAY, "0" * 64) == ()


def test_persistent_lease_cooldown_rate_and_credit_accounting(tmp_path: Path) -> None:
    store = store_at(tmp_path)
    other = CurrentDataStore(tmp_path / "data.sqlite3")
    policy = AccountPolicy(
        attempts_per_60_seconds=1, provenance="Synthetic configured account limit"
    )
    store.reserve("account", "first", 1000, 1020, "/time_series", 1, policy)
    with pytest.raises(DataUnavailable, match="busy"):
        other.reserve("account", "second", 1001, 1021, "/stocks", None, None)
    store.release("account", "wrong_owner")
    with pytest.raises(DataUnavailable, match="busy"):
        other.reserve("account", "second", 1001, 1021, "/stocks", None, None)
    store.release("account", "first", 1100)
    with pytest.raises(DataUnavailable, match="cooldown"):
        other.reserve("account", "second", 1050, 1070, "/stocks", None, None)
    other.reserve("account", "second", 1101, 1121, "/stocks", None, policy)
    other.release("account", "second")
    with pytest.raises(DataUnavailable, match="rate_limit"):
        store.reserve("account", "third", 1102, 1122, "/time_series", 1, policy)
    assert store.usage("account") == (2, 1, 1)
    credit = AccountPolicy(
        credits_per_86400_seconds=100, provenance="Synthetic configured credit limit"
    )
    with pytest.raises(DataUnavailable, match="credit_cost_unknown"):
        store.reserve("account", "third", 1200, 1220, "/time_series", 1, credit)


def test_run_symbol_attempt_deadline_and_cancel_guards() -> None:
    run = DispatchRun(BarInterval.ONE_DAY)
    assert run.limits.daily_attempts == 6 and run.limits.daily_symbols == 5
    assert run.limits.intraday_attempts == 3 and run.limits.intraday_symbols == 2
    run.attempts = 6
    with pytest.raises(DataUnavailable, match="attempt_budget"):
        run.check_attempt("AAA")
    run.attempts = 0
    run.symbols = {"A", "B", "C", "D", "E"}
    with pytest.raises(DataUnavailable, match="symbol_budget"):
        run.check_attempt("AAA")
    run.check_attempt("A")  # Retries spend attempts, not a new symbol slot.
    run.cancellation.set()
    with pytest.raises(DataUnavailable, match="cancelled"):
        run.remaining()
    run = DispatchRun(BarInterval.ONE_DAY, started=0)
    with pytest.raises(DataUnavailable, match="deadline"):
        run.remaining()


@pytest.mark.anyio
async def test_gateway_retry_attempt_cap_and_unknown_catalogue_cost(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    store, p = store_at(tmp_path), plan()
    run = DispatchRun(BarInterval.ONE_DAY, DispatchLimits(daily_attempts=1))
    async with SharedDispatcher(store, "account").gateway(
        run, "test-key", httpx.MockTransport(handler)
    ) as gateway:
        with pytest.raises(DataUnavailable, match="attempt_budget"):
            await gateway.get_historical_bars(
                "AAA", p.required[0].open_at, p.required[-1].open_at, BarInterval.ONE_DAY
            )
    assert calls == run.attempts == 1 and store.usage("account") == (1, 1, 0)
    run = DispatchRun(BarInterval.ONE_DAY)
    async with SharedDispatcher(store, "account").gateway(
        run,
        "test-key",
        httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "symbol": "AAA",
                            "name": "Fixture",
                            "exchange": "NASDAQ",
                            "country": "United States",
                            "type": "Common Stock",
                            "currency": "USD",
                        }
                    ]
                },
            )
        ),
    ) as gateway:
        await gateway.list_stocks()
    assert run.attempts == 1 and run.unknown_credit_attempts == 1


@pytest.mark.anyio
async def test_history_chunk_accounting_cache_reuse_and_truncation(tmp_path: Path) -> None:
    store, p, calls = store_at(tmp_path), plan(), 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.params["prepost"] == "false"
        return httpx.Response(200, json=payload(p, request))

    class Clock:
        def now(self) -> datetime:
            return p.cutoff.cutoff_at

    run = DispatchRun(BarInterval.ONE_DAY)
    async with SharedDispatcher(store, "account").gateway(
        run, "test-key", httpx.MockTransport(handler)
    ) as gateway:
        service = CurrentHistoryDataService(store, gateway, Clock(), chunk_size=4)
        assert (await service.load(p)).freshness.status == "current"
        assert (await service.load(p)).freshness.status == "current"
    assert calls == run.attempts == 2
    store = CurrentDataStore(tmp_path / "truncated.sqlite3")
    store.initialize()

    def truncated(request: httpx.Request) -> httpx.Response:
        body = payload(p, request)
        body["values"] = body["values"][:3]
        return httpx.Response(200, json=body)

    async with SharedDispatcher(store, "account").gateway(
        DispatchRun(BarInterval.ONE_DAY), "test-key", httpx.MockTransport(truncated)
    ) as gateway:
        with pytest.raises(DataUnavailable, match="missing_expected_candle"):
            await CurrentHistoryDataService(store, gateway, Clock()).load(p)
    assert len(store.load("AAA", BarInterval.ONE_DAY, p.policy_key)) == 3


@pytest.mark.anyio
async def test_later_chunk_failure_preserves_first_chunk(tmp_path: Path) -> None:
    store, p, calls = store_at(tmp_path), plan(), 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=payload(p, request)) if calls == 1 else httpx.Response(503)

    class Clock:
        def now(self) -> datetime:
            return p.cutoff.cutoff_at

    run = DispatchRun(BarInterval.ONE_DAY)
    async with SharedDispatcher(store, "account").gateway(
        run, "test-key", httpx.MockTransport(handler)
    ) as gateway:
        with pytest.raises(ProviderNetworkError):
            await CurrentHistoryDataService(store, gateway, Clock(), chunk_size=4).load(p)
    assert calls == run.attempts == 3
    assert len(store.load("AAA", BarInterval.ONE_DAY, p.policy_key)) == 4


@pytest.mark.anyio
async def test_rate_limit_cooldown_and_active_cancellation(tmp_path: Path) -> None:
    store, p = store_at(tmp_path), plan()
    run = DispatchRun(BarInterval.ONE_DAY)
    async with SharedDispatcher(store, "account").gateway(
        run,
        "test-key",
        httpx.MockTransport(
            lambda _: httpx.Response(
                429,
                headers={"Retry-After": "120"},
                json={"status": "error", "code": 429, "message": "API credits exhausted"},
            )
        ),
    ) as gateway:
        with pytest.raises(RateLimitError):
            await gateway.get_historical_bars(
                "AAA", p.required[0].open_at, p.required[-1].open_at, BarInterval.ONE_DAY
            )
        with pytest.raises(DataUnavailable, match="cooldown"):
            await gateway.list_stocks()
    assert run.attempts == 1


@pytest.mark.anyio
async def test_deadline_ends_request_and_releases_lease(tmp_path: Path) -> None:
    store, p = store_at(tmp_path), plan()
    calls = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(10)
        return httpx.Response(503)

    run = DispatchRun(BarInterval.ONE_DAY, DispatchLimits(timeout_seconds=0.05))
    async with SharedDispatcher(store, "account").gateway(
        run, "test-key", httpx.MockTransport(slow)
    ) as gateway:
        with pytest.raises(DataUnavailable, match="deadline"):
            await gateway.get_historical_bars(
                "AAA", p.required[0].open_at, p.required[-1].open_at, BarInterval.ONE_DAY
            )
    assert calls <= 1 and run.attempts <= 1
    with sqlite3.connect(tmp_path / "data.sqlite3") as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM provider_coordination WHERE owner IS NOT NULL"
            ).fetchone()[0]
            == 0
        )


@pytest.mark.anyio
async def test_symbol_failure_cooldown_avoids_second_http_attempt(tmp_path: Path) -> None:
    store, p = store_at(tmp_path), plan()

    class Clock:
        def now(self) -> datetime:
            return p.cutoff.cutoff_at

    run = DispatchRun(BarInterval.ONE_DAY)
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            400,
            json={
                "status": "error",
                "code": 400,
                "message": "No data is available on the specified dates.",
            },
        )
    )
    async with SharedDispatcher(store, "account").gateway(run, "test-key", transport) as gateway:
        service = CurrentHistoryDataService(store, gateway, Clock())
        with pytest.raises(NoDataError):
            await service.load(p)
        with pytest.raises(DataUnavailable, match="symbol_cooldown"):
            await service.load(p)
    assert run.attempts == 1


@pytest.mark.anyio
async def test_active_cancellation_stops_http(tmp_path: Path) -> None:
    store, p = store_at(tmp_path), plan()
    run = DispatchRun(BarInterval.ONE_DAY)

    async def blocked(request: httpx.Request) -> httpx.Response:
        run.cancellation.set()
        await asyncio.sleep(100)
        return httpx.Response(500)

    async with SharedDispatcher(store, "different-account").gateway(
        run, "test-key", httpx.MockTransport(blocked)
    ) as gateway:
        with pytest.raises(DataUnavailable, match="cancelled"):
            await gateway.get_historical_bars(
                "AAA", p.required[0].open_at, p.required[-1].open_at, BarInterval.ONE_DAY
            )
    assert run.attempts == 1
