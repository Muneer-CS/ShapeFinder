from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from math import exp
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from shape_finder.api.current_scan_schemas import CurrentPatternScanRequest
from shape_finder.application.current_pattern_scan import ScanControl
from shape_finder.application.manual_pattern_scan import ManualPatternScanService
from shape_finder.application.massive_daily import (
    cached_history,
    refresh_daily,
    required_sessions,
    scan_cached_daily,
)
from shape_finder.config import Settings
from shape_finder.core.candle_eligibility import FrozenCutoff
from shape_finder.core.current_data import DataUnavailable
from shape_finder.core.universe import SymbolMetadata
from shape_finder.infrastructure.market_data.massive import CATALOGUE, EASTERN, MassiveClient
from shape_finder.infrastructure.persistence.current_data_store import CurrentDataStore
from shape_finder.infrastructure.persistence.massive_store import DailyBar, MassiveStore
from shape_finder.main import create_app

CUTOFF = datetime(2026, 11, 30, 16, tzinfo=UTC)
PUBLICATION = time(20, 15)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def listing(symbol: str, mic: str) -> dict[str, object]:
    return {
        "ticker": symbol,
        "name": symbol,
        "primary_exchange": mic,
        "locale": "us",
        "market": "stocks",
        "type": "CS",
        "active": True,
    }


def grouped(day: date, symbols: tuple[str, ...]) -> dict[str, object]:
    stamp = int(datetime.combine(day, time(16), EASTERN).timestamp() * 1000)
    return {
        "status": "OK",
        "adjusted": True,
        "results": [
            {
                "T": s,
                "t": stamp,
                "o": 104,
                "h": 106,
                "l": 102,
                "c": 100 + day.day % 5 + 2,
                "v": 1000,
            }
            for s in symbols
        ],
    }


def full_request(minimum: int, maximum: int, similarity: float) -> CurrentPatternScanRequest:
    return CurrentPatternScanRequest.model_validate(
        {
            "pattern": {
                "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}],
                "provenance": {"kind": "drawing"},
            },
            "revisions": {"pattern_revision": 1, "settings_revision": 1},
            "settings": {
                "interval": "1day",
                "match_type": "full",
                "minimum_similarity": similarity,
                "minimum_movement_percent": 1,
                "duration": {"minimum_candles": minimum, "maximum_candles": maximum},
                "top_n": 100,
            },
            "universe": {"kind": "nasdaq"},
        }
    )


def seed_scan(
    path: Path, prices: dict[str, list[Decimal | None]]
) -> tuple[MassiveStore, tuple[SymbolMetadata, ...], tuple[date, ...]]:
    CurrentDataStore(path).initialize()
    store = MassiveStore(path)
    members = tuple(
        SymbolMetadata(s, s, "NASDAQ", "United States", "Common Stock", "USD") for s in prices
    )
    dates = tuple(
        item.session_date
        for item in required_sessions(CUTOFF, len(next(iter(prices.values()))), PUBLICATION)
    )
    store.replace_catalog(members, CUTOFF)
    for i, day in enumerate(dates):
        bars = [
            DailyBar(s, price, price, price, price, Decimal(200))
            for s, row in prices.items()
            if (price := row[i]) is not None
        ]
        if bars:
            store.save_session(day, bars, CUTOFF, CUTOFF)
    return store, members, dates


@pytest.mark.anyio
async def test_full_endpoint_scans_past_cap_and_uses_latest_recent_window(tmp_path: Path) -> None:
    path = tmp_path / "massive.sqlite3"
    rising = [Decimal(100) * Decimal("1.03") ** i for i in range(18)]
    symbols = [f"T{i:04d}" for i in range(217)]
    store, _, days = seed_scan(
        path,
        {
            **dict.fromkeys(symbols, rising),
            "RECENT": [*rising[:-2], None, None],
            "STALE": [*rising[:-3], None, None, None],
            "HIST": [*rising[:10], *reversed(rising[:8])],
            "GAP": [*rising[:-5], None, *rising[-4:]],
            "MISSING": [None] * 18,
            "INVALID": [*rising[:-1], Decimal("NaN")],
        },
    )
    settings = Settings(
        _env_file=None,
        database_path=path,
        current_daily_provider="massive",
        current_candidate_limit=200,
    )
    factory = Mock(side_effect=AssertionError("Massive scanning should only read SQLite"))
    service = ManualPatternScanService(
        settings,
        CurrentDataStore(path),
        clock=Mock(now=lambda: CUTOFF),
        transport_factory=factory,
    )
    app = create_app(settings=settings, current_scan_service=service)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        response = await client.post(
            "/api/v1/pattern-scans", json=full_request(8, 8, 90).model_dump(mode="json")
        )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["universe_snapshot"]["selected_candidates"] == 223
    assert result["universe_snapshot"]["selection_policy"] == "all_exchange_members"
    assert result["coverage"] == {
        "universe_total": 223,
        "scanned": 219,
        "eligible": 219,
        "stale": 1,
        "skipped": 2,
        "failed": 1,
        "not_evaluated": 0,
        "evaluation_incomplete": 0,
        "matches_returned": 100,
    }
    assert result["provider_usage"]["http_attempts"] == 0
    assert result["execution"]["stop_reasons"] == []
    assert result["reason_counts"] == {
        "massive_daily_history_gap": 1,
        "malformed_history": 1,
        "missing_expected_candle": 1,
        "massive_daily_stale_candle": 1,
    }
    assert result["evaluated_session_dates"]["RECENT"] == days[-3].isoformat()
    assert result["evaluated_session_dates"]["HIST"] == days[-1].isoformat()
    assert result["evaluated_session_dates"][symbols[-1]] == days[-1].isoformat()
    assert all(
        m["match_type"] == "full"
        and m["symbol"] in symbols
        and m["last_candle_session"] == days[-1].isoformat()
        and m["matched_start_session"] == days[-8].isoformat()
        for m in result["matches"]
    )
    match = result["matches"][0]
    expected = store.history(match["symbol"], days[-8], days[-1])
    samples = match["comparison"]["observations"]
    assert [
        datetime.fromisoformat(s["timestamp"]).astimezone(EASTERN).date() for s in samples
    ] == list(days[-8:])
    assert [
        tuple(Decimal(s[field]) for field in ("open", "high", "low", "close", "volume"))
        for s in samples
    ] == [expected[day][0][1:] for day in days[-8:]]
    factory.assert_not_called()


def test_full_ranges_rank_recency_then_similarity_and_do_not_bridge_gaps(tmp_path: Path) -> None:
    rising = [Decimal(100) * Decimal("1.03") ** i for i in range(14)]
    store, members, days = seed_scan(
        tmp_path / "ranges.sqlite3",
        {
            "STRONG": [*rising[:-2], None, None],
            "WEAK": [*rising[:-1], rising[-1] * 2],
            "SHAPED": [*rising[:-10], Decimal(300), Decimal(250), *rising[-8:]],
            "SHORT": [*rising[:-9], None, *rising[-8:]],
        },
    )

    def evaluate(minimum: int, maximum: int) -> dict[str, tuple[float, int, date | None]]:
        request = full_request(minimum, maximum, 0)
        result, _ = scan_cached_daily(
            store,
            request.pattern,
            request.settings,
            FrozenCutoff(cutoff_at=CUTOFF),
            members,
            PUBLICATION,
            ScanControl(),
            1_000,
        )
        return {
            m.symbol: (m.similarity_score, m.observed_candles, m.last_candle_session)
            for m in result.matches
        }

    matches = evaluate(8, 10)
    assert tuple(matches)[-1] == "STRONG"
    assert matches["STRONG"][0] > matches["WEAK"][0]
    assert matches["STRONG"][2] == date(2026, 11, 24) == days[-3]
    assert matches["SHORT"][1:] == (8, days[-1])
    assert matches["SHAPED"][1:] == (8, days[-1])
    assert matches["SHAPED"][0] > evaluate(9, 9)["SHAPED"][0]
    assert matches["SHAPED"][0] > evaluate(10, 10)["SHAPED"][0]


def test_full_scan_does_not_fall_back_to_an_older_matching_window(tmp_path: Path) -> None:
    rising = [Decimal(100) * Decimal("1.03") ** i for i in range(9)]
    store, members, dates = seed_scan(
        tmp_path / "history.sqlite3", {"HIST": [*rising, *reversed(rising[:8])]}
    )
    request = full_request(8, 8, 95)

    def scan(at: datetime):
        return scan_cached_daily(
            store,
            request.pattern,
            request.settings,
            FrozenCutoff(cutoff_at=at),
            members,
            PUBLICATION,
            ScanControl(),
            100,
        )

    old_cutoff = datetime.combine(dates[8] + timedelta(days=1), time(10), EASTERN)
    old, _ = scan(old_cutoff)
    assert len(old.matches) == 1 and old.matches[0].last_candle_session == dates[8]
    current, evaluated = scan(CUTOFF)
    assert current.coverage.scanned == 1 and current.matches == ()
    assert evaluated == {"HIST": dates[-1]}


@pytest.mark.parametrize("reason", ["deadline", "cancelled", "computation_budget"])
def test_full_scan_retains_first_batch_when_stopped(tmp_path: Path, reason: str) -> None:
    rising = [Decimal(100 + i) for i in range(10)]
    store, members, _ = seed_scan(
        tmp_path / "partial.sqlite3", dict.fromkeys((f"X{i:03d}" for i in range(150)), rising)
    )
    read = store.histories
    now = 0.0
    cancelled = False
    calls = 0

    def read_batch(symbols: list[str], start: date, end: date) -> object:
        nonlocal now, cancelled, calls
        calls += 1
        if calls == 2:
            now = float(reason == "deadline")
            cancelled = reason == "cancelled"
        return read(symbols, start, end)

    store.histories = Mock(side_effect=read_batch)
    request = full_request(8, 8, 0)
    result, dates = scan_cached_daily(
        store,
        request.pattern,
        request.settings,
        FrozenCutoff(cutoff_at=CUTOFF),
        members,
        PUBLICATION,
        ScanControl(deadline_at=1, monotonic=lambda: now, cancelled=lambda: cancelled),
        128 if reason == "computation_budget" else 1000,
    )
    assert calls == 2
    assert result.coverage.scanned == len(dates) == 128
    assert result.coverage.not_evaluated == 22
    assert result.execution.partial and result.execution.stop_reasons == (reason,)
    assert result.execution.hypotheses_evaluated == 128
    assert {item.reason for item in result.exclusions} == {reason}


def test_forming_daily_thresholds_recency_and_modes(tmp_path: Path) -> None:
    # Exactly 60% ends on a pullback. Extending the reference would add another
    # reversal. Neither an upward line nor a window starting later is equivalent.
    levels = (
        0,
        0.16,
        0.29,
        0.44,
        0.58,
        0.78,
        1,
        0.94,
        0.81,
        0.68,
        0.59,
        0.47,
        0.33,
        0.52,
        0.79,
        1.1,
        1.35,
        1.29,
        1.17,
        0.89,
        0.72,
    )
    prices = [Decimal(str(100 * exp(y / 5))) for y in levels]
    weak = [Decimal(str(100 * exp((y + 0.15 * (i % 2)) / 5))) for i, y in enumerate(levels[:13])]
    generic = [Decimal("100") * Decimal("1.015") ** i for i in range(13)]

    def pad(values: list[Decimal], lag: int = 0) -> list[Decimal | None]:
        return [None] * (25 - len(values) - lag) + values + [None] * lag

    gap = pad(prices[:13])
    gap[-4] = None
    store, members, dates = seed_scan(
        tmp_path / "forming.sqlite3",
        {
            "EDGE": pad(prices[:13]),
            "BELOW": pad(prices[:12]),
            "NEAR": pad(prices[:20]),
            "RECENT": pad(prices[:13], lag=2),
            "STALE": pad(prices[:13], lag=3),
            "GAP": gap,
            "WEAK": pad(weak),
            "GENERIC": pad(generic),
            "COMPLETE": pad(prices),
            "DUAL": pad([Decimal(500), Decimal(50)] * 4 + prices[:13]),
        },
    )
    body = full_request(21, 21, 0).model_dump(mode="json")
    body["pattern"]["points"] = [{"x": i / 20, "y": y} for i, y in enumerate(levels)]

    def scan(mode: str, completion: float, similarity: float):
        body["settings"].update(
            match_type=mode, minimum_completion=completion, minimum_similarity=similarity
        )
        request = CurrentPatternScanRequest.model_validate(body)
        return scan_cached_daily(
            store,
            request.pattern,
            request.settings,
            FrozenCutoff(cutoff_at=CUTOFF),
            members,
            PUBLICATION,
            ScanControl(),
            1_000,
        )[0]

    unfiltered = scan("forming", 0.6, 0)
    forming = {match.symbol: match for match in unfiltered.matches}
    assert forming["EDGE"].similarity_score == pytest.approx(100)
    assert forming["EDGE"].similarity_score > forming["WEAK"].similarity_score
    assert forming["WEAK"].similarity_score > forming["GENERIC"].similarity_score
    assert forming["EDGE"].observed_candles == 13
    assert forming["EDGE"].complete_duration_candles == 21
    assert forming["EDGE"].estimated_completion == 0.6
    assert forming["EDGE"].matched_start_session == dates[-13]
    assert forming["EDGE"].last_candle_session == dates[-1]
    assert forming["NEAR"].observed_candles == 20
    assert forming["NEAR"].estimated_completion == 0.95
    assert forming["NEAR"].matched_start_session == dates[-20]
    assert forming["NEAR"].similarity_score == pytest.approx(100)
    assert forming["RECENT"].last_candle_session == dates[-3]
    assert unfiltered.matches[-1].symbol == "RECENT"
    assert all(
        m.match_type == "forming"
        and m.freshness.is_complete
        and m.comparison is not None
        and len(m.comparison.observations) == m.observed_candles
        for m in forming.values()
    )
    assert {item.symbol: item.reason for item in unfiltered.exclusions} == {
        "BELOW": "massive_daily_history_gap",
        "GAP": "massive_daily_history_gap",
        "STALE": "massive_daily_stale_candle",
    }

    at_cutoff = scan("forming", 0.6, 95)
    assert {m.symbol for m in at_cutoff.matches} == {"DUAL", "EDGE", "NEAR", "RECENT"}
    assert {m.symbol for m in scan("forming", 0.600001, 95).matches} == {"NEAR"}
    assert {m.symbol for m in scan("forming", 0.95, 95).matches} == {"NEAR"}
    with pytest.raises(ValueError, match="No forming completion"):
        scan("forming", 0.950001, 95)
    assert {m.symbol for m in scan("forming", 0.55, 0).matches} >= {"BELOW", "EDGE"}
    all_matches = scan("all", 0.6, 0).matches
    assert len({m.symbol for m in all_matches}) == len(all_matches)
    complete = {m.symbol: m for m in scan("full", 0.6, 0).matches}
    assert set(complete) == {"COMPLETE", "DUAL"}
    assert all(m.match_type == "full" for m in complete.values())
    both = {m.symbol: m for m in all_matches}
    assert both["COMPLETE"] == complete["COMPLETE"]
    assert forming["COMPLETE"].match_type == "forming"
    assert forming["DUAL"].similarity_score > complete["DUAL"].similarity_score
    assert both["DUAL"] == forming["DUAL"]
    assert both["EDGE"] == forming["EDGE"]


def test_publication_uses_exchange_dates_across_holiday_weekend_and_early_close() -> None:
    boundary = datetime(2026, 11, 28, 1, 15, tzinfo=UTC)
    before = required_sessions(boundary - timedelta(seconds=1), 2, PUBLICATION)
    after = required_sessions(boundary, 2, PUBLICATION)
    assert [c.session_date for c in before] == [date(2026, 11, 24), date(2026, 11, 25)]
    assert [c.session_date for c in after] == [date(2026, 11, 25), date(2026, 11, 27)]
    assert after[-1].close_at.astimezone(EASTERN).hour == 13
    assert required_sessions(CUTOFF, 2, PUBLICATION) == after


def test_refresh_paginates_resumes_reuses_and_keeps_last_complete_catalogue(tmp_path: Path) -> None:
    current = CurrentDataStore(tmp_path / "data.sqlite3")
    current.initialize()
    store = MassiveStore(current.path)
    days = [c.session_date for c in required_sessions(CUTOFF, 3, PUBLICATION)]
    requests: list[str] = []
    failed, interrupted = False, False
    now = CUTOFF.timestamp()
    starts: list[float] = []

    def advance(seconds: float) -> None:
        nonlocal now
        now += seconds

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal failed
        starts.append(now)
        assert "apiKey" not in request.url.params
        path = request.url.path
        requests.append(path)
        if path == CATALOGUE:
            cursor = request.url.params.get("cursor")
            if interrupted and cursor:
                return httpx.Response(503)
            if cursor:
                return httpx.Response(
                    200, json={"status": "OK", "results": [listing("MSFT", "XNAS")]}
                )
            if request.url.params["exchange"] == "XNAS":
                return httpx.Response(
                    200,
                    json={
                        "status": "OK",
                        "results": [listing("AAPL", "XNAS")],
                        "next_url": "https://api.massive.com/v3/reference/tickers?cursor=two&apiKey=omit",
                    },
                )
            return httpx.Response(200, json={"status": "OK", "results": [listing("IBM", "XNYS")]})
        day = date.fromisoformat(path.rsplit("/", 1)[-1])
        assert request.url.params["adjusted"] == "true"
        if day == days[1] and not failed:
            failed = True
            return httpx.Response(200, json=grouped(day, ()))
        symbols = ("AAPL", "IBM") if day == days[0] else ("AAPL",)
        return httpx.Response(200, json=grouped(day, symbols))

    with httpx.Client(
        base_url="https://api.massive.com", transport=httpx.MockTransport(handle)
    ) as http:
        adapter = MassiveClient(http, current, clock=lambda: now, sleep=advance)
        result = refresh_daily(store, adapter, CUTOFF, 3, PUBLICATION, timedelta(days=1))
        assert result["pending_sessions"] == [days[1].isoformat()]
        assert len(requests) == 6 and requests.count(CATALOGUE) == 3
        assert starts[5] - starts[0] >= 60
        repeated = refresh_daily(store, adapter, CUTOFF, 3, PUBLICATION, timedelta(days=1))
        assert not repeated["pending_sessions"]
        assert requests[-1].endswith(days[1].isoformat()) and len(requests) == 7
        refresh_daily(store, adapter, CUTOFF, 3, PUBLICATION, timedelta(days=1))
        assert len(requests) == 7
        assert set(store.history("AAPL", days[0], days[-1])) == set(days)
        assert set(store.history("IBM", days[0], days[-1])) == {days[0]}
        original = store.catalog_snapshot()
        interrupted = True
        with pytest.raises(DataUnavailable, match="massive_http_503"):
            refresh_daily(
                store, adapter, CUTOFF, 3, PUBLICATION, timedelta(days=1), force_catalogue=True
            )
        assert store.catalog_snapshot() == original
        assert current.catalog_snapshot()[0] == ()

    required = required_sessions(CUTOFF, 3, PUBLICATION)
    cutoff = FrozenCutoff(cutoff_at=CUTOFF)
    series = cached_history(store, "AAPL", required, cutoff, PUBLICATION, "fixture").series
    assert series is not None and series.bars[-1].timestamp == required[-1].open_at
    with pytest.raises(DataUnavailable, match="missing_expected_candle"):
        cached_history(store, "IBM", required, cutoff, PUBLICATION, "fixture")


@pytest.mark.parametrize("invalid", ["date", "duplicate", "ohlcv"])
def test_invalid_batch_is_rejected(tmp_path: Path, invalid: str) -> None:
    current = CurrentDataStore(tmp_path / "data.sqlite3")
    current.initialize()
    day = date(2026, 11, 27)
    body = grouped(day, ("AAPL", "AAPL"))
    rows = body["results"]
    assert isinstance(rows, list)
    if invalid == "date":
        rows[0]["t"] += 86400000
    if invalid == "ohlcv":
        rows[0]["c"] = -1
    with (
        httpx.Client(
            base_url="https://api.massive.com",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body)),
        ) as http,
        pytest.raises(DataUnavailable, match="massive_invalid_session_bar"),
    ):
        MassiveClient(http, current).grouped(day)


@pytest.mark.anyio
async def test_named_daily_endpoint_scans_massive_cache_without_transport(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None, database_path=tmp_path / "data.sqlite3", current_daily_provider="massive"
    )
    current = CurrentDataStore(settings.database_path)
    current.initialize()
    store = MassiveStore(settings.database_path)
    candles = required_sessions(CUTOFF, 8, PUBLICATION)
    aapl, msft = listing("AAPL", "XNAS"), listing("MSFT", "XNAS")

    def handle(req: httpx.Request) -> httpx.Response:
        if req.url.path == CATALOGUE:
            rows = (
                [aapl, msft] if req.url.params["exchange"] == "XNAS" else [listing("IBM", "XNYS")]
            )
            return httpx.Response(200, json={"status": "OK", "results": rows})
        day = date.fromisoformat(req.url.path.rsplit("/", 1)[-1])
        tickers = ("AAPL", "IBM") if day == candles[2].session_date else ("AAPL", "MSFT", "IBM")
        return httpx.Response(200, json=grouped(day, tickers))

    with httpx.Client(
        base_url="https://api.massive.com", transport=httpx.MockTransport(handle)
    ) as client:
        timer = Mock(side_effect=(CUTOFF.timestamp() + i * 15 for i in range(50)))
        adapter = MassiveClient(client, current, clock=timer)
        refresh_daily(store, adapter, CUTOFF, 8, PUBLICATION, timedelta(days=1))

    factory = Mock(side_effect=AssertionError("Scan must not create a provider transport"))
    service = ManualPatternScanService(
        settings, current, clock=Mock(now=lambda: CUTOFF), transport_factory=factory
    )
    request = CurrentPatternScanRequest.model_validate(
        {
            "pattern": {
                "points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}],
                "provenance": {"kind": "drawing"},
            },
            "revisions": {"pattern_revision": 1, "settings_revision": 1},
            "settings": {
                "interval": "1day",
                "duration": {"minimum_candles": 8, "maximum_candles": 8},
            },
            "universe": {"kind": "nasdaq"},
        }
    )
    app = create_app(settings=settings, current_scan_service=service)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
    ):
        for kind, symbol, total in [("nasdaq", "AAPL", 2), ("nyse", "IBM", 1)]:
            body = request.model_dump(mode="json")
            body["universe"]["kind"] = kind
            response = await client.post("/api/v1/pattern-scans", json=body)
            assert response.status_code == 200, response.text
            result = response.json()
            assert result["provider_usage"]["http_attempts"] == 0
            assert (
                result["coverage"]["scanned"] == 1 and result["coverage"]["universe_total"] == total
            )
            assert result["evaluated_session_dates"] == {
                symbol: candles[-1].session_date.isoformat()
            }
            if kind == "nasdaq":
                assert result["reason_counts"]["massive_daily_history_gap"] == 1
    factory.assert_not_called()
