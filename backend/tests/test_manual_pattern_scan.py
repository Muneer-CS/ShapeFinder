import asyncio
import gc
import json
import math
import sqlite3
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from shape_finder.api.current_scan_schemas import CurrentPatternScanRequest
from shape_finder.api.pattern_routes import _error, pattern_scan
from shape_finder.api.safety import SearchAdmissionController
from shape_finder.application.current_pattern_scan import CurrentPatternScanner
from shape_finder.application.manual_pattern_scan import (
    CandidateRing,
    ManualPatternScanService,
    PatternScanUnavailable,
    ScanCancellation,
)
from shape_finder.config import Settings
from shape_finder.core.candle_eligibility import FrozenCutoff
from shape_finder.core.current_data import DataUnavailable, DispatchLimits
from shape_finder.core.universe import SymbolMetadata
from shape_finder.infrastructure.market_data.current_dispatch import DispatchRun
from shape_finder.infrastructure.persistence.current_data_store import CurrentDataStore
from shape_finder.infrastructure.persistence.migration_backup import backup_before_v4
from shape_finder.infrastructure.persistence.migrations import MIGRATIONS
from shape_finder.infrastructure.persistence.sqlite_market_data import SQLiteMarketDataRepository
from shape_finder.main import create_app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def no_live_http(monkeypatch: pytest.MonkeyPatch) -> None:
    async def denied(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Live HTTP disabled in current-scan integration tests")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", denied)


class Clock:
    def now(self) -> datetime:
        return datetime(2025, 7, 16, 20, 3, tzinfo=UTC)


def request(**settings: Any) -> CurrentPatternScanRequest:
    return CurrentPatternScanRequest.model_validate(
        {
            "pattern": {
                "points": [{"x": 0, "y": 0}, {"x": 0.5, "y": 1}, {"x": 1, "y": 0}],
                "provenance": {"kind": "drawing"},
            },
            "revisions": {"pattern_revision": 7, "settings_revision": 3},
            "settings": {
                "duration": {"minimum_candles": 21, "maximum_candles": 21},
                "minimum_similarity": 80,
                "minimum_movement_percent": 0,
                **settings,
            },
            "universe": {"kind": "nasdaq"},
        }
    )


def listing(symbol: str) -> SymbolMetadata:
    return SymbolMetadata(symbol, symbol, "NASDAQ", "United States", "Common Stock", "USD")


def setup(
    tmp_path: Path, *, symbols: tuple[str, ...] = ("AAA",), policies: bool = True, **changes: Any
) -> tuple[ManualPatternScanService, list[httpx.Request]]:
    store = CurrentDataStore(tmp_path / "data.sqlite3")
    store.initialize()
    store.repository._replace_universe_sync(
        tuple(listing(s) for s in symbols), Clock().now(), "fixture"
    )
    values: dict[str, Any] = dict(
        _env_file=None, database_path=store.repository.path, twelve_data_api_key="offline-test-key"
    )
    if policies:
        values["current_timing_policies"] = [
            {
                "interval": "1day",
                "publication_delay_seconds": 60,
                "finalization_delay_seconds": 120,
                "basis": "verified",
                "reviewed_at": Clock().now(),
                "provenance": "Synthetic offline test attestation, not a real provider guarantee",
            }
        ]
    values.update(changes)
    calls: list[httpx.Request] = []
    service = ManualPatternScanService(Settings(**values), store, clock=Clock())
    plan, _ = service._cached(listing(symbols[0]), request(), FrozenCutoff.freeze(Clock()))
    sessions = plan.required

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        symbol = req.url.params["symbol"]
        return httpx.Response(
            200,
            json={
                "meta": {"symbol": symbol, "exchange_timezone": "America/New_York"},
                "values": [
                    {
                        "datetime": c.session_date.isoformat(),
                        "open": str(100 * math.exp(0.1 * (1 - abs(i - 10) / 10))),
                        "high": "120",
                        "low": "90",
                        "close": str(100 * math.exp(0.1 * (1 - abs(i - 10) / 10))),
                        "volume": "1000",
                    }
                    for i, c in enumerate(sessions)
                    if req.url.params["start_date"][:10]
                    <= c.session_date.isoformat()
                    < req.url.params["end_date"][:10]
                ],
            },
        )

    service.transport_factory = lambda: httpx.MockTransport(handler)
    return service, calls


def test_capabilities_no_io_and_operator_labels(tmp_path: Path) -> None:
    # Constructor and inspection do not need a calendar, database initialization or HTTP.
    s = Settings(_env_file=None, database_path=tmp_path / "unused.sqlite3")
    service = ManualPatternScanService(s, CurrentDataStore(s.database_path))
    assert all(not cap.available for cap in service.capabilities().intervals)
    assert not s.database_path.exists()
    with pytest.raises(PatternScanUnavailable, match="publication_finalization_unknown"):
        service.validate_capability(request())
    policy = {
        "interval": "1day",
        "publication_delay_seconds": 60,
        "finalization_delay_seconds": 120,
        "basis": "configured",
        "provenance": "Synthetic operator policy, not verified",
        "reviewed_at": Clock().now(),
    }
    for accepted in (False, True):
        cfg = Settings(
            _env_file=None,
            current_timing_policies=[policy],
            current_accept_configured_policies=accepted,
        )
        cap = ManualPatternScanService(cfg, service.store).capabilities().intervals[-1]
        assert cap.status == "configured_but_unverified" and cap.available == accepted
        assert (
            cap.timing_basis == "operator_configured" and not cap.provider_timing_guarantee_verified
        )


def test_candidate_ring_progress_is_stable() -> None:
    members = tuple(listing(s) for s in ("AAA", "BBB", "CCC", "DDD"))
    a, b = CandidateRing(), CandidateRing()
    first = a.select(members, "nasdaq1day", 2)
    second = a.select(members, "nasdaq1day", 2)
    assert first == b.select(tuple(reversed(members)), "nasdaq1day", 2)
    assert {m.symbol for m in first}.isdisjoint(m.symbol for m in second)
    assert a.select(members, "nasdaq1day", 2) == first


def test_unavailable_envelope_retains_request_identity() -> None:
    req = request()
    http = SimpleNamespace(state=SimpleNamespace(request_id="offline-request"))
    response = _error(http, PatternScanUnavailable("publication_finalization_unknown"), req)
    error = json.loads(response.body)["error"]
    assert response.status_code == 503 and error["details"] is None
    assert error["revisions"] == req.revisions.model_dump()
    assert error["pattern_content_hash"] == req.pattern.content_hash()
    assert error["reason"] == "publication_finalization_unknown"


def test_wal_backup_before_upgrade_and_disposable_restore(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at_utc TEXT)"
        )
        for migration in MIGRATIONS[:3]:
            for statement in migration.statements:
                writer.execute(statement)
            writer.execute(
                "INSERT INTO schema_migrations VALUES (?,?)", (migration.version, "fixture")
            )
        writer.execute("CREATE TABLE sentinel(value TEXT)")
        writer.execute("INSERT INTO sentinel VALUES ('committed WAL observation')")
        writer.commit()
        repo = SQLiteMarketDataRepository(path)
        repo._initialize_sync()
        backup = repo.last_migration_backup
        assert backup is not None and backup.parent == tmp_path / "backups"
        with (
            sqlite3.connect(backup) as source,
            sqlite3.connect(tmp_path / "restored.sqlite3") as restored,
        ):
            assert source.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 3
            assert (
                source.execute("SELECT value FROM sentinel").fetchone()[0]
                == "committed WAL observation"
            )
            source.backup(restored)
            assert restored.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert writer.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == len(
            MIGRATIONS
        )
        assert backup_before_v4(path) is None


def test_backup_failure_prevents_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at_utc TEXT)"
        )
        db.execute("INSERT INTO schema_migrations VALUES(3,'fixture')")

    def fail(_: Path) -> None:
        raise OSError("Disposable backup failure")

    monkeypatch.setattr(
        "shape_finder.infrastructure.persistence.sqlite_market_data.backup_before_v4", fail
    )
    with pytest.raises(OSError):
        SQLiteMarketDataRepository(path)._initialize_sync()
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 3


def test_openapi_declares_current_scan_schemas() -> None:
    api = create_app(settings=Settings(_env_file=None)).openapi()
    assert "/api/v1/pattern-scans" in api["paths"]
    assert "/api/v1/pattern-scans/capabilities" in api["paths"]
    responses = api["paths"]["/api/v1/pattern-scans"]["post"]["responses"]
    assert {"200", "422", "429", "503", "504"} <= set(responses)


@pytest.mark.parametrize("interval", ["1min", "5min", "15min", "30min", "1h"])
def test_interval_planning_window_covers_maximum_duration(tmp_path: Path, interval: str) -> None:
    service, calls = setup(tmp_path)
    policy = service.settings.current_timing_policies[0].model_copy(
        update={
            "interval": interval,
            "session_open_buckets_confirmed": True,
            "bucket_provenance": "Synthetic bucket test policy, not provider verification",
        }
    )
    service.settings = service.settings.model_copy(update={"current_timing_policies": (policy,)})
    req = request(interval=interval, duration={"minimum_candles": 20, "maximum_candles": 240})
    plan, cached = service._cached(listing("AAA"), req, FrozenCutoff.freeze(Clock()))
    assert len(plan.required) == 240 and cached is None
    assert plan.required[-1].interval.value == interval and calls == []


@pytest.mark.anyio
async def test_complete_service_flow_warm_cache_hash_and_zero_matches(tmp_path: Path) -> None:
    service, calls = setup(tmp_path)
    req = request(match_type="full", minimum_similarity=99.9)
    result = await service.scan(req, ScanCancellation())
    assert result.coverage.scanned == 1 and result.outcome == "evaluated"
    assert result.matches[0].match_type == "full" and result.matches[0].similarity_score > 99.9
    assert (
        result.revisions == req.revisions
        and result.pattern_content_hash == req.pattern.content_hash()
    )
    plan, _ = service._cached(listing("AAA"), req, result.cutoff)
    assert result.matches[0].freshness.expected == plan.required[-1]
    assert result.provider_usage.http_attempts == len(calls) == 1
    warm = await service.scan(req, ScanCancellation())
    assert warm.provider_usage.http_attempts == 0 and len(calls) == 1
    negative = req.model_dump()
    negative["pattern"]["points"][1]["y"] = -1
    no_match = await service.scan(
        CurrentPatternScanRequest.model_validate(negative), ScanCancellation()
    )
    assert (
        no_match.matches == ()
        and no_match.coverage.scanned == 1
        and no_match.outcome == "evaluated"
    )


@pytest.mark.anyio
async def test_full_forming_filter_reconsiders_alignments(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    req = request(minimum_similarity=0)
    p, _ = service._cached(listing("AAA"), req, FrozenCutoff.freeze(Clock()))

    def data(req: httpx.Request) -> httpx.Response:
        closes = [100 - i for i in range(10)] + [100 * math.exp(i / 100) for i in range(11)]
        return httpx.Response(
            200,
            json={
                "meta": {"symbol": "AAA", "exchange_timezone": "America/New_York"},
                "values": [
                    {
                        "datetime": c.session_date.isoformat(),
                        "open": str(v),
                        "high": str(v),
                        "low": str(v),
                        "close": str(v),
                        "volume": "1000",
                    }
                    for c, v in zip(p.required, closes, strict=True)
                ],
            },
        )

    service.transport_factory = lambda: httpx.MockTransport(data)
    all_result = await service.scan(req, ScanCancellation())
    assert all_result.matches[0].match_type == "forming"
    assert all_result.matches[0].estimated_completion == 0.5
    full = await service.scan(request(match_type="full", minimum_similarity=0), ScanCancellation())
    assert full.matches[0].match_type == "full"
    assert full.matches[0].similarity_score < all_result.matches[0].similarity_score


@pytest.mark.anyio
async def test_partial_hydration_and_coverage_partition(tmp_path: Path) -> None:
    service, calls = setup(
        tmp_path,
        symbols=("AAA", "BBB", "CCC"),
        current_dispatch_limits=DispatchLimits(daily_symbols=1),
    )
    result = await service.scan(request(), ScanCancellation())
    c = result.coverage
    assert c.scanned == 1 and c.not_evaluated == 2 and c.eligible >= c.scanned
    assert c.scanned + c.stale + c.skipped + c.failed + c.not_evaluated == c.universe_total == 3
    assert result.outcome == "partial" and result.execution.stop_reasons == ("provider_budget",)
    assert len(calls) == result.provider_usage.http_attempts == 1


@pytest.mark.anyio
async def test_capability_and_disconnect_before_attempts(
    tmp_path: Path, recwarn: pytest.WarningsRecorder
) -> None:
    tasks_before = asyncio.all_tasks()
    service, calls = setup(tmp_path)
    service.settings = service.settings.model_copy(update={"current_timing_policies": ()})
    with pytest.raises(PatternScanUnavailable, match="publication_finalization_unknown"):
        await service.scan(request(), ScanCancellation())
    assert calls == []
    service, calls = setup(tmp_path)
    cancel = ScanCancellation()
    cancel.stop()
    with pytest.raises(PatternScanUnavailable):
        await service.scan(request(), cancel)
    assert calls == []
    await asyncio.sleep(0)
    gc.collect()
    assert not (asyncio.all_tasks() - tasks_before)
    assert not [str(w.message) for w in recwarn if issubclass(w.category, RuntimeWarning)]


@pytest.mark.parametrize("reason,status", [("cancelled", 499), ("deadline", 504)])
def test_snapshot_guard_does_not_abandon_coroutine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recwarn: pytest.WarningsRecorder,
    reason: str,
    status: int,
) -> None:
    service, calls = setup(tmp_path)
    cancel = ScanCancellation()
    if reason == "cancelled":
        cancel.stop()
    else:
        monkeypatch.setattr(DispatchRun, "remaining", Mock(side_effect=DataUnavailable(reason)))
    scheduler = Mock(side_effect=AssertionError("Rejected scan must not schedule work"))
    monkeypatch.setattr(asyncio, "wait_for", scheduler)
    monkeypatch.setattr(asyncio, "create_task", scheduler)
    operation = service.scan(request(), cancel)
    try:
        # A rejected pre-scheduling path must finish before its first suspension.
        # This drives the actual production coroutine, without creating a host event loop.
        with pytest.raises(PatternScanUnavailable) as failure:
            operation.send(None)
        assert failure.value.reason == reason and failure.value.status == status
    finally:
        operation.close()
    gc.collect()
    scheduler.assert_not_called()
    assert calls == [] and service.store.usage(service.settings.current_account_scope) == (0, 0, 0)
    assert not [str(w.message) for w in recwarn if issubclass(w.category, RuntimeWarning)]


def test_public_lifecycle_capability_rejection_and_validation(tmp_path: Path) -> None:
    service, calls = setup(tmp_path)
    service.settings = service.settings.model_copy(update={"current_timing_policies": ()})
    app = create_app(settings=service.settings, current_scan_service=service)
    with TestClient(app) as client:
        assert calls == []
        response = client.get("/api/v1/pattern-scans/capabilities")
        assert response.status_code == 200 and calls == []
        unavailable = client.post("/api/v1/pattern-scans", json=request().model_dump(mode="json"))
        assert unavailable.status_code == 503
        assert unavailable.json()["error"]["reason"] == "publication_finalization_unknown"
        invalid = request().model_dump(mode="json")
        invalid["settings"]["minimum_similarity"] = None
        assert client.post("/api/v1/pattern-scans", json=invalid).status_code == 422
        assert calls == []


def test_public_success_and_revision_propagation(tmp_path: Path) -> None:
    service, calls = setup(tmp_path)
    with TestClient(create_app(settings=service.settings, current_scan_service=service)) as client:
        reply = client.post(
            "/api/v1/pattern-scans", json=request(match_type="full").model_dump(mode="json")
        )
        assert reply.status_code == 200
        body = reply.json()
        assert body["coverage"]["scanned"] == 1 and body["revisions"]["pattern_revision"] == 7
        assert body["pattern_content_hash"] == request().pattern.content_hash()
        assert body["provider_usage"]["http_attempts"] == len(calls) == 1
        match = body["matches"][0]
        samples = match["comparison"]["observations"]
        assert len(samples) == match["observed_candles"] == 21
        assert samples[0]["timestamp"] == match["start_at"]
        assert samples[-1]["timestamp"] == match["freshness"]["expected"]["open_at"]
        assert match["comparison"]["log_relative_closes"][0] == 0
        assert len(calls) == 1  # Comparisons never fetch extra prices.


@pytest.mark.anyio
async def test_daily_publication_reports_session_without_matches_and_reuses_cache(
    tmp_path: Path,
) -> None:
    service, calls = setup(
        tmp_path,
        current_accept_configured_policies=True,
        current_timing_policies=[
            {
                "interval": "1day",
                "next_trading_day_publication": True,
                "reviewed_at": Clock().now(),
                "provenance": "Synthetic configured publication policy",
            }
        ],
    )
    body = request(minimum_movement_percent=1000)
    cold = await service.scan(body, ScanCancellation())
    assert cold.coverage.scanned == 1 and not cold.matches
    assert cold.evaluated_session_dates == {"AAA": date(2025, 7, 15)}
    assert cold.provider_usage.http_attempts == len(calls) == 1
    warm = await service.scan(body, ScanCancellation())
    assert warm.evaluated_session_dates == cold.evaluated_session_dates
    assert warm.provider_usage.http_attempts == 0 and len(calls) == 1


@pytest.mark.anyio
async def test_configured_policy_is_explicit_in_real_response(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    policies = tuple(
        p.model_copy(update={"basis": "configured"})
        for p in service.settings.current_timing_policies
    )
    service.settings = service.settings.model_copy(
        update={"current_timing_policies": policies, "current_accept_configured_policies": True}
    )
    result = await service.scan(request(), ScanCancellation())
    assert result.capability.status == "configured_but_unverified"
    assert result.capability.timing_basis == "operator_configured"
    assert not result.capability.provider_timing_guarantee_verified


@pytest.mark.anyio
async def test_stale_refresh_failure_preserves_other_completed_stock(tmp_path: Path) -> None:
    service, _ = setup(tmp_path, symbols=("AAA", "BBB"))
    await service.scan(request(), ScanCancellation())
    with sqlite3.connect(service.store.path) as db:
        db.execute("DELETE FROM current_candles WHERE symbol='BBB'")
    calls: list[httpx.Request] = []

    def unavailable(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(503)

    service.transport_factory = lambda: httpx.MockTransport(unavailable)
    result = await service.scan(request(), ScanCancellation())
    assert result.coverage.scanned == 1 and result.coverage.failed == 1
    assert result.execution.stop_reasons == ("provider_error",)
    assert result.provider_usage.http_attempts == len(calls) == 2
    assert [m.symbol for m in result.matches] == ["AAA"]


@pytest.mark.anyio
async def test_disconnect_cancels_active_http_and_releases_admission(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)
    started = asyncio.Event()

    async def blocked(req: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.sleep(10)
        return httpx.Response(503)

    service.transport_factory = lambda: httpx.MockTransport(blocked)
    admission = SearchAdmissionController(1)

    class Request:
        app = SimpleNamespace(
            state=SimpleNamespace(current_pattern_service=service, search_admission=admission)
        )
        state = SimpleNamespace(request_id="offline-test")

        async def is_disconnected(self) -> bool:
            return started.is_set()

    result = await asyncio.wait_for(pattern_scan(request(), Request()), timeout=2)
    assert result.status_code == 499 and admission._active == 0


@pytest.mark.anyio
async def test_completed_scan_stops_watcher_even_if_poll_consumes_cancellation() -> None:
    polling = asyncio.Event()
    consumed = asyncio.Event()
    polls = 0
    admission = SearchAdmissionController(1)

    async def complete(*args: object) -> JSONResponse:
        await polling.wait()
        return JSONResponse({"outcome": "evaluated"})

    service = SimpleNamespace(scan=complete, validate_capability=lambda _: None)

    class Request:
        app = SimpleNamespace(
            state=SimpleNamespace(current_pattern_service=service, search_admission=admission)
        )

        async def is_disconnected(self) -> bool:
            nonlocal polls
            polls += 1
            if polls > 1:
                return True
            polling.set()
            try:
                await asyncio.Future[None]()
            except asyncio.CancelledError:
                consumed.set()
            return False

    result = await asyncio.wait_for(pattern_scan(request(), Request()), timeout=2)
    assert result.status_code == 200
    assert consumed.is_set() and polls == 1
    assert admission._active == 0


@pytest.mark.anyio
async def test_cancelled_route_holds_admission_until_worker_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _ = setup(tmp_path)
    await service.scan(request(), ScanCancellation())  # Warm data before delaying only CPU work.
    entered, release = threading.Event(), threading.Event()
    original = CurrentPatternScanner.scan

    def paused(self: Any, *args: Any, **kwargs: Any) -> Any:
        entered.set()
        release.wait(2)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(CurrentPatternScanner, "scan", paused)
    admission = SearchAdmissionController(1)

    class Request:
        app = SimpleNamespace(
            state=SimpleNamespace(current_pattern_service=service, search_admission=admission)
        )
        state = SimpleNamespace(request_id="offline-test")

        async def is_disconnected(self) -> bool:
            return False

    task = asyncio.create_task(pattern_scan(request(), Request()))
    try:
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert admission._active == 1
    finally:
        release.set()
    async with asyncio.timeout(2):
        while admission._active:
            await asyncio.sleep(0.005)
    assert admission._active == 0


@pytest.mark.anyio
async def test_attempt_budget_and_expired_deadline_are_structured(tmp_path: Path) -> None:
    service, calls = setup(tmp_path, current_dispatch_limits=DispatchLimits(daily_attempts=1))
    with pytest.raises(PatternScanUnavailable) as expired:
        await service.scan(request(), ScanCancellation(), started=time.monotonic() - 31)
    assert expired.value.status == 504 and calls == []
    attempts: list[httpx.Request] = []

    def failure(req: httpx.Request) -> httpx.Response:
        attempts.append(req)
        return httpx.Response(503)

    service.transport_factory = lambda: httpx.MockTransport(failure)
    with pytest.raises(PatternScanUnavailable) as unavailable:
        await service.scan(request(), ScanCancellation())
    assert unavailable.value.status == 503
    details = unavailable.value.details
    assert details is not None and details.coverage.scanned == 0
    assert details.provider_usage.http_attempts == len(attempts) == 1
    assert details.execution.stop_reasons == ("provider_budget",)


@pytest.mark.anyio
async def test_missing_latest_cache_endpoint_is_refreshed(tmp_path: Path) -> None:
    service, calls = setup(tmp_path)
    first = await service.scan(request(), ScanCancellation())
    latest = first.matches[0].freshness.expected.open_at.isoformat()
    with sqlite3.connect(service.store.path) as db:
        db.execute("DELETE FROM current_candles WHERE open_at=?", (latest,))
    result = await service.scan(request(), ScanCancellation())
    assert result.coverage.scanned == 1 and result.provider_usage.http_attempts == 1
    assert len(calls) == 2
    assert calls[-1].url.params["start_date"] == "2025-07-16"
    assert calls[-1].url.params["end_date"] == "2025-07-17"


@pytest.mark.anyio
async def test_provider_cooldown_keeps_warm_stock_usable(tmp_path: Path) -> None:
    service, _ = setup(tmp_path, symbols=("AAA", "BBB"))
    seed = request().model_dump()
    seed["universe"] = {"kind": "custom", "symbols": ["AAA"]}
    await service.scan(CurrentPatternScanRequest.model_validate(seed), ScanCancellation())
    service.transport_factory = lambda: httpx.MockTransport(
        lambda _: httpx.Response(
            429, json={"status": "error", "code": 429, "message": "API credits exhausted"}
        )
    )
    limited = await service.scan(request(), ScanCancellation())
    assert limited.coverage.scanned == 1 and limited.provider_usage.http_attempts == 1
    again = await service.scan(request(), ScanCancellation())
    assert again.coverage.scanned == 1 and again.provider_usage.http_attempts == 0
    assert again.reason_counts["provider_cooldown"] == 1


@pytest.mark.anyio
async def test_cutoff_does_not_move_with_retrieval_clock(tmp_path: Path) -> None:
    service, _ = setup(tmp_path)

    class AdvancingClock:
        calls = 0

        def now(self) -> datetime:
            self.calls += 1
            return Clock().now() + timedelta(days=self.calls - 1)

    clock = AdvancingClock()
    service.clock = clock
    result = await service.scan(request(), ScanCancellation())
    assert result.cutoff.cutoff_at == Clock().now()
    assert result.matches[0].freshness.cutoff_at == result.cutoff.cutoff_at
    assert result.matches[0].freshness.retrieved_at > result.cutoff.cutoff_at
