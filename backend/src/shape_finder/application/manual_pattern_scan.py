"""Manual scan orchestration. No startup fetches, legacy provider bypass, or demo data."""

import asyncio
import hashlib
import math
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from datetime import date, datetime, timedelta
from datetime import time as wall_time
from importlib.metadata import version
from typing import TypeVar
from uuid import uuid4

import httpx

from shape_finder.api.current_scan_schemas import (
    CurrentPatternScanRequest,
    CurrentPatternScanResponse,
    IntervalCapability,
    PatternCapabilities,
    ProviderUsage,
    UniverseSnapshot,
)
from shape_finder.application.current_history import (
    CurrentHistoryDataService,
    HistoryPlan,
    assess_history,
    plan_history,
)
from shape_finder.application.current_pattern_scan import (
    CurrentPatternScanner,
    EligibleHistory,
    ScanControl,
)
from shape_finder.application.massive_daily import scan_cached_daily
from shape_finder.application.universe import _normalize_catalog, _select
from shape_finder.config import Settings
from shape_finder.core.candle_eligibility import Clock, FrozenCutoff, SystemClock
from shape_finder.core.current_data import DataUnavailable
from shape_finder.core.errors import MarketDataError, MissingApiKeyError
from shape_finder.core.market_data import BarInterval
from shape_finder.core.pattern_scan import Coverage, ExecutionStatus
from shape_finder.core.universe import SymbolMetadata, UniverseKind
from shape_finder.infrastructure.exchange_calendar import ListingCalendar
from shape_finder.infrastructure.market_data.current_capabilities import capability_report
from shape_finder.infrastructure.market_data.current_dispatch import DispatchRun, SharedDispatcher
from shape_finder.infrastructure.persistence.current_data_store import CurrentDataStore
from shape_finder.infrastructure.persistence.massive_store import MassiveStore

T = TypeVar("T")
CalendarFactory = Callable[[SymbolMetadata, date, date], ListingCalendar]


class PatternScanUnavailable(Exception):
    def __init__(
        self, reason: str, status: int = 503, details: CurrentPatternScanResponse | None = None
    ) -> None:
        super().__init__(reason)
        self.reason, self.status, self.details = reason, status, details


class ScanCancellation:
    def __init__(self) -> None:
        self.thread = threading.Event()
        self.http = asyncio.Event()

    def stop(self) -> None:
        self.thread.set()
        self.http.set()


async def _offload(call: Callable[[], T]) -> T:
    task = asyncio.create_task(asyncio.to_thread(call))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # A Python worker cannot be killed. Keep its owning service/admission alive.
        await asyncio.shield(task)
        raise


class CandidateRing:
    """Stable hash order, advancing process-local cursor per frozen membership/interval."""

    def __init__(self) -> None:
        self._cursors: dict[str, int] = {}

    def select(
        self, items: Sequence[SymbolMetadata], key: str, limit: int
    ) -> tuple[SymbolMetadata, ...]:
        ordered = sorted(items, key=lambda x: hashlib.sha256((key + x.symbol).encode()).digest())
        digest = hashlib.sha256((key + "|".join(x.symbol for x in ordered)).encode()).hexdigest()
        if not ordered:
            return ()
        start = self._cursors.get(digest, 0) % len(ordered)
        count = min(limit, len(ordered))
        self._cursors[digest] = (start + count) % len(ordered)
        # Avoid unbounded cursor growth after catalogue churn.
        if len(self._cursors) > 128:
            self._cursors = {digest: self._cursors[digest]}
        return tuple(ordered[(start + i) % len(ordered)] for i in range(count))


class ManualPatternScanService:
    def __init__(
        self,
        settings: Settings,
        store: CurrentDataStore,
        *,
        clock: Clock | None = None,
        calendar_factory: CalendarFactory = ListingCalendar,
        transport_factory: Callable[[], httpx.AsyncBaseTransport] | None = None,
    ) -> None:
        self.settings, self.store = settings, store
        self.massive_store = MassiveStore(store.path)
        self.clock = clock or SystemClock()
        self.calendar_factory, self.transport_factory = calendar_factory, transport_factory
        self.dispatcher = SharedDispatcher(
            store, settings.current_account_scope, settings.current_account_policy
        )
        self.ring = CandidateRing()
        self._api_key = (
            settings.twelve_data_api_key.get_secret_value()
            if settings.twelve_data_api_key
            else None
        )

    def _uses_massive(self, interval: BarInterval, universe: UniverseKind | None) -> bool:
        return (
            interval is BarInterval.ONE_DAY
            and self.settings.current_daily_provider == "massive"
            and universe in (None, UniverseKind.NASDAQ, UniverseKind.NYSE)
        )

    def interval_capability(
        self, interval: BarInterval, universe: UniverseKind | None = None
    ) -> IntervalCapability:
        if self._uses_massive(interval, universe):
            return IntervalCapability(
                interval=interval.value,
                available=True,
                status="configured_but_unverified",
                reason="massive_daily_cache",
                timing_basis="operator_configured",
            )
        policy = next(
            (p for p in self.settings.current_timing_policies if p.interval == interval.value), None
        )
        report = capability_report(interval, policy)
        accepted = report.status == "usable" or (
            report.status == "configured_but_unverified"
            and self.settings.current_accept_configured_policies
        )
        return IntervalCapability(
            interval=interval.value,
            status=report.status,
            available=accepted,
            reason=(
                report.reason
                if accepted or report.status == "unavailable"
                else "configured_policy_requires_explicit_acceptance"
            ),
            timing_basis="unknown"
            if policy is None
            else ("operator_attested" if policy.basis == "verified" else "operator_configured"),
        )

    def capabilities(self) -> PatternCapabilities:
        return PatternCapabilities(
            calendar_version=version("exchange_calendars"),
            intervals=tuple(
                self.interval_capability(i) for i in BarInterval if i is not BarInterval.ONE_WEEK
            ),
            hydration_configured=bool(self._api_key),
            accept_configured_policies=self.settings.current_accept_configured_policies,
        )

    def validate_capability(self, request: CurrentPatternScanRequest) -> IntervalCapability:
        cap = self.interval_capability(
            BarInterval(request.settings.interval), request.universe.kind
        )
        if not cap.available:
            raise PatternScanUnavailable(cap.reason)
        if request.settings.include_unfinished:
            raise PatternScanUnavailable(cap.unfinished_reason)
        return cap

    def _cached(
        self, listing: SymbolMetadata, request: CurrentPatternScanRequest, cutoff: FrozenCutoff
    ) -> tuple[HistoryPlan, EligibleHistory | None]:
        day = cutoff.cutoff_at.date()
        minutes = {"1min": 1, "5min": 5, "15min": 15, "30min": 30, "1h": 60}
        if request.settings.interval == "1day":
            span = request.settings.duration.maximum_candles * 2 + 30
        elif request.settings.interval == "1min":
            span = 14  # Keep the one-minute schedule below the adapter's 5000-candle cap.
        else:
            span = (
                math.ceil(
                    request.settings.duration.maximum_candles
                    * minutes[request.settings.interval]
                    / 390
                )
                * 2
                + 14
            )
        # This is a bounded planning window, NOT an inferred trading calendar.
        # plan_history still requires the exact real schedule and adequate count.
        cal = self.calendar_factory(
            listing,
            max(date(2000, 1, 1), day - timedelta(days=span)),
            min(date(2030, 12, 31), day + timedelta(days=1)),
        )
        policy = next(
            (
                p
                for p in self.settings.current_timing_policies
                if p.interval == request.settings.interval
            ),
            None,
        )
        plan = plan_history(
            listing.symbol,
            cal,
            capability_report(BarInterval(request.settings.interval), policy),
            cutoff,
            request.settings.duration.maximum_candles,
            accept_configured_policy=self.settings.current_accept_configured_policies,
        )
        rows = self.store.load(listing.symbol, plan.report.capability.interval, plan.policy_key)
        try:
            return plan, assess_history(plan, rows)
        except DataUnavailable:
            return plan, None

    async def scan(
        self,
        request: CurrentPatternScanRequest,
        cancellation: ScanCancellation,
        started: float | None = None,
    ) -> CurrentPatternScanResponse:
        cap = self.validate_capability(request)
        started = time.monotonic() if started is None else started
        deadline = started + self.settings.current_scan_timeout_seconds
        cutoff = FrozenCutoff.freeze(self.clock)
        remaining = min(
            self.settings.current_dispatch_limits.timeout_seconds, deadline - time.monotonic()
        )
        if remaining <= 0:
            raise PatternScanUnavailable("deadline", 504)
        limits = self.settings.current_dispatch_limits.model_copy(
            update={"timeout_seconds": remaining}
        )
        run = DispatchRun(
            BarInterval(request.settings.interval), limits, cancellation=cancellation.http
        )
        massive = self._uses_massive(run.interval, request.universe.kind)
        publication = wall_time.fromisoformat(self.settings.massive_publication_time_et)
        ready: list[EligibleHistory] = []
        terminal: dict[str, str] = {}
        reasons: Counter[str] = Counter()
        stops: set[str] = set()
        async with (
            nullcontext()
            if massive
            else self.dispatcher.gateway(
                run, self._api_key, self.transport_factory() if self.transport_factory else None
            )
        ) as gateway:

            async def snapshot() -> tuple[tuple[SymbolMetadata, ...], datetime | None]:
                store = self.massive_store if massive else self.store
                catalog, refreshed = await _offload(store.catalog_snapshot)
                if not catalog:
                    raise DataUnavailable("universe_catalog_unavailable")
                return _normalize_catalog(catalog), refreshed

            try:
                timeout = run.remaining()
                catalog, refreshed = await asyncio.wait_for(snapshot(), timeout=timeout)
            except (MarketDataError, DataUnavailable, TimeoutError) as error:
                reason = str(error) if isinstance(error, DataUnavailable) else type(error).__name__
                raise PatternScanUnavailable(
                    reason,
                    504
                    if isinstance(error, TimeoutError) or reason == "deadline"
                    else (499 if reason == "cancelled" else 503),
                ) from error
            by_symbol = {item.symbol: item for item in catalog}
            if request.universe.kind is UniverseKind.CUSTOM:
                if any(symbol not in by_symbol for symbol in request.universe.symbols):
                    raise PatternScanUnavailable("listing_metadata_unavailable")
                members = tuple(by_symbol[symbol] for symbol in request.universe.symbols)
            else:
                members = _select(catalog, request.universe.kind)
            if not members or len(members) > 50_000:
                raise PatternScanUnavailable("universe_empty_or_too_large")
            selected = (
                members
                if massive
                else self.ring.select(
                    members,
                    request.universe.kind.value + request.settings.interval,
                    self.settings.current_candidate_limit,
                )
            )
            selected_symbols = {s.symbol for s in selected}
            for member in members:
                if member.symbol not in selected_symbols:
                    terminal[member.symbol] = "not_evaluated"
                    reasons["candidate_limit"] += 1
            if len(selected) < len(members):
                stops.add("candidate_limit")
            missing: list[HistoryPlan] = []

            def exclude(symbol: str, category: str, reason: str) -> None:
                terminal[symbol] = category
                reasons[reason] += 1

            for member in () if massive else selected:

                def cached_for_member(
                    item: SymbolMetadata = member,
                ) -> tuple[HistoryPlan, EligibleHistory | None]:
                    return self._cached(item, request, cutoff)

                try:
                    timeout = run.remaining()
                    plan, cached = await asyncio.wait_for(
                        _offload(cached_for_member),
                        timeout=timeout,
                    )
                    if cached is not None:
                        ready.append(cached)
                    else:
                        missing.append(plan)
                except (DataUnavailable, ValueError, TimeoutError) as error:
                    reason = (
                        str(error)
                        if isinstance(error, DataUnavailable)
                        else ("deadline" if isinstance(error, TimeoutError) else "invalid_history")
                    )
                    category = "not_evaluated" if reason in {"deadline", "cancelled"} else "skipped"
                    exclude(member.symbol, category, reason)
                    if category == "not_evaluated":
                        stops.add(reason)
            symbol_cap = (
                limits.daily_symbols
                if run.interval is BarInterval.ONE_DAY
                else limits.intraday_symbols
            )
            blocked: str | None = None
            loader = CurrentHistoryDataService(self.store, gateway, self.clock) if gateway else None
            for plan in missing:
                assert loader is not None
                if blocked or len(run.symbols) >= symbol_cap:
                    exclude(plan.symbol, "not_evaluated", blocked or "hydration_symbol_limit")
                    stops.add("provider_budget" if not blocked else blocked)
                    continue
                try:
                    ready.append(await loader.load(plan))
                except (MarketDataError, DataUnavailable, TimeoutError, ValueError) as error:
                    reason = (
                        str(error) if isinstance(error, DataUnavailable) else type(error).__name__
                    )
                    if reason in {
                        "deadline",
                        "cancelled",
                        "http_attempt_budget",
                        "provider_busy",
                        "provider_cooldown",
                        "configured_rate_limit",
                        "configured_credit_limit",
                        "credit_cost_unknown",
                    }:
                        blocked = (
                            reason if reason in {"deadline", "cancelled"} else "provider_budget"
                        )
                        exclude(plan.symbol, "not_evaluated", reason)
                        stops.add(blocked)
                    elif reason == "symbol_cooldown":
                        exclude(plan.symbol, "skipped", reason)
                    else:
                        exclude(plan.symbol, "failed", reason)
                        stops.add("provider_error")
                        if isinstance(error, MissingApiKeyError) or type(error).__name__ in {
                            "AuthenticationError",
                            "RateLimitError",
                            "DailyQuotaError",
                            "ProviderNetworkError",
                        }:
                            blocked = "provider_error"
        # Keep completed cache batches when the scan reaches its deadline or is cancelled.
        if massive:
            control = ScanControl(deadline_at=deadline, cancelled=cancellation.thread.is_set)
            evaluation, evaluated_sessions = await _offload(
                lambda: scan_cached_daily(
                    self.massive_store,
                    request.pattern,
                    request.settings,
                    cutoff,
                    selected,
                    publication,
                    control,
                    self.settings.current_hypothesis_limit,
                )
            )
        else:
            control = ScanControl(
                deadline_at=min(deadline, time.monotonic() + self.settings.current_scoring_seconds),
                cancelled=cancellation.thread.is_set,
            )
            scanner = CurrentPatternScanner(max_hypotheses=self.settings.current_hypothesis_limit)
            evaluation = await _offload(
                lambda: scanner.scan(
                    request.pattern, request.settings, cutoff, ready, control=control
                )
            )
            excluded_symbols = {item.symbol for item in evaluation.exclusions}
            evaluated_sessions = {
                history.symbol: history.freshness.expected.session_date
                for history in ready
                if request.settings.interval == "1day"
                and history.symbol not in terminal
                and history.symbol not in excluded_symbols
                and history.freshness.expected is not None
            }
        for item in evaluation.exclusions:
            terminal[item.symbol] = item.category
            reasons[item.reason] += 1
        stops.update(evaluation.execution.stop_reasons)
        counts = Counter(terminal.values())
        coverage = Coverage(
            universe_total=len(members),
            scanned=evaluation.coverage.scanned,
            eligible=max(
                evaluation.coverage.eligible,
                len(ready)
                - evaluation.coverage.failed
                - evaluation.coverage.skipped
                - evaluation.coverage.stale,
            ),
            stale=counts["stale"],
            skipped=counts["skipped"],
            failed=counts["failed"],
            not_evaluated=counts["not_evaluated"],
            evaluation_incomplete=evaluation.coverage.evaluation_incomplete,
            matches_returned=len(evaluation.matches),
        )
        partial = coverage.scanned < coverage.universe_total
        execution = ExecutionStatus.model_validate(
            {
                "partial": partial,
                "stop_reasons": sorted(stops),
                "provider_requests": run.attempts,
                "hypotheses_evaluated": evaluation.execution.hypotheses_evaluated,
            }
        )
        result = CurrentPatternScanResponse(
            scan_id=uuid4().hex,
            revisions=request.revisions,
            cutoff=cutoff,
            pattern_content_hash=request.pattern.content_hash(),
            settings=request.settings,
            matches=evaluation.matches,
            coverage=coverage,
            execution=execution,
            outcome="partial" if partial else "evaluated",
            capability=cap,
            provider_usage=ProviderUsage(
                http_attempts=run.attempts,
                documented_credit_cost=run.documented_credit_cost,
                unknown_cost_attempts=run.unknown_credit_attempts,
            ),
            universe_snapshot=UniverseSnapshot(
                kind=request.universe.kind.value,
                frozen_at=cutoff.cutoff_at,
                catalog_refreshed_at=refreshed,
                catalog_stale=refreshed is None
                or (
                    cutoff.cutoff_at - refreshed > timedelta(hours=self.settings.universe_ttl_hours)
                ),
                selected_candidates=len(selected),
                selection_policy=(
                    "all_exchange_members" if massive else "sha256_ring_process_local_v1"
                ),
            ),
            reason_counts=dict(reasons),
            evaluated_session_dates=evaluated_sessions,
        )
        if not coverage.scanned:
            raise PatternScanUnavailable(
                "deadline"
                if "deadline" in stops
                else (
                    "cancelled"
                    if "cancelled" in stops
                    else (
                        "missing_expected_candle"
                        if reasons["missing_expected_candle"]
                        else (
                            "massive_daily_history_gap"
                            if reasons["massive_daily_history_gap"]
                            else "no_stocks_evaluated"
                        )
                    )
                ),
                504 if "deadline" in stops else (499 if "cancelled" in stops else 503),
                result,
            )
        return result
