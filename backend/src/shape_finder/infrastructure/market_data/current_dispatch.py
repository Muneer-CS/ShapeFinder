"""The sole HTTP boundary for the new data path; legacy provider remains compatible."""

import asyncio
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from shape_finder.core.current_data import AccountPolicy, DataUnavailable, DispatchLimits
from shape_finder.core.errors import MalformedProviderResponseError
from shape_finder.core.market_data import BarInterval, TimeSeries
from shape_finder.core.universe import SymbolMetadata
from shape_finder.infrastructure.market_data.twelve_data import TwelveDataProvider
from shape_finder.infrastructure.persistence.current_data_store import CurrentDataStore


@dataclass
class DispatchRun:
    interval: BarInterval
    limits: DispatchLimits = field(default_factory=DispatchLimits)
    attempts: int = 0
    documented_credit_cost: int = 0
    unknown_credit_attempts: int = 0
    actual_credits_consumed: None = None  # No account-usage reconciliation is performed here.
    symbols: set[str] = field(default_factory=set)
    cancellation: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    started: float = field(default_factory=time.monotonic)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def remaining(self) -> float:
        if self.cancellation.is_set():
            raise DataUnavailable("cancelled")
        remaining = self.started + self.limits.timeout_seconds - time.monotonic()
        if remaining <= 0:
            raise DataUnavailable("deadline")
        return remaining

    def check_attempt(self, symbol: str | None) -> None:
        self.remaining()
        daily = self.interval is BarInterval.ONE_DAY
        if self.attempts >= (
            self.limits.daily_attempts if daily else self.limits.intraday_attempts
        ):
            raise DataUnavailable("http_attempt_budget")
        if (
            symbol is not None
            and symbol not in self.symbols
            and len(self.symbols)
            >= (self.limits.daily_symbols if daily else self.limits.intraday_symbols)
        ):
            raise DataUnavailable("symbol_budget")


class SharedDispatcher:
    def __init__(
        self, store: CurrentDataStore, account_scope: str, policy: AccountPolicy | None = None
    ) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", account_scope):
            raise ValueError("Use a non-secret stable account coordination label.")
        self.store, self.scope, self.policy = store, account_scope, policy

    def gateway(
        self,
        run: DispatchRun,
        api_key: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> "CurrentDataGateway":
        return CurrentDataGateway(self, run, api_key, transport or httpx.AsyncHTTPTransport())


class _DispatchTransport(httpx.AsyncBaseTransport):
    def __init__(
        self, shared: SharedDispatcher, run: DispatchRun, inner: httpx.AsyncBaseTransport
    ) -> None:
        self.shared, self.run, self.inner = shared, run, inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method != "GET" or request.url.host != "api.twelvedata.com":
            raise DataUnavailable("dispatch_target_unavailable")
        endpoint = request.url.path
        symbol = request.url.params.get("symbol") if endpoint == "/time_series" else None
        if endpoint not in {"/time_series", "/stocks"} or (
            endpoint == "/time_series"
            and (
                symbol is None
                or re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", symbol) is None
                or request.url.params.get("interval") != self.run.interval.value
            )
        ):
            raise DataUnavailable("dispatch_request_unavailable")
        # Official time_series docs specify 1 credit per symbol. Catalogue cost is unknown here.
        cost = 1 if endpoint == "/time_series" else None
        owner = uuid.uuid4().hex
        async with self.run.lock:
            self.run.check_attempt(symbol)
            remaining = self.run.remaining()
            now = time.time()
            reservation = asyncio.create_task(
                asyncio.to_thread(
                    self.shared.store.reserve,
                    self.shared.scope,
                    owner,
                    now,
                    now + remaining,
                    endpoint,
                    cost,
                    self.shared.policy,
                )
            )
            try:
                await asyncio.shield(reservation)
            except asyncio.CancelledError:
                # The short DB operation may finish after task cancellation. Do not
                # leave a successful reservation leased merely because its await was cancelled.
                await asyncio.gather(reservation, return_exceptions=True)
                await asyncio.shield(
                    asyncio.to_thread(self.shared.store.release, self.shared.scope, owner)
                )
                raise
            self.run.attempts += 1
            if symbol is not None:
                self.run.symbols.add(symbol)
            if cost is None:
                self.run.unknown_credit_attempts += 1
            else:
                self.run.documented_credit_cost += cost
        cooldown = 0.0

        async def exchange() -> httpx.Response:
            response = await self.inner.handle_async_request(request)
            try:
                await response.aread()  # Keep the lease through the body, not only headers.
                return response
            finally:
                await response.aclose()

        work: asyncio.Task[httpx.Response] | None = None
        cancelled: asyncio.Task[bool] | None = None
        try:
            remaining = self.run.remaining()
            work = asyncio.create_task(exchange())
            cancelled = asyncio.create_task(self.run.cancellation.wait())
            done, _ = await asyncio.wait(
                (work, cancelled), timeout=remaining, return_when=asyncio.FIRST_COMPLETED
            )
            if cancelled in done:
                raise DataUnavailable("cancelled")
            if work not in done:
                raise DataUnavailable("deadline")
            response = work.result()
            if response.status_code in {401, 403, 429}:
                delay = (
                    self.run.limits.cooldown_seconds
                )  # Local backoff, NOT a verified reset time.
                retry = response.headers.get("retry-after")
                if retry:
                    try:
                        value = float(retry)
                    except ValueError:
                        try:
                            value = parsedate_to_datetime(retry).timestamp() - time.time()
                        except (ValueError, TypeError, OverflowError):
                            value = 0
                    if math.isfinite(value):
                        delay = max(delay, value)
                cooldown = time.time() + delay
            return response
        finally:
            for task in (work, cancelled):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(t for t in (work, cancelled) if t is not None), return_exceptions=True
            )
            await asyncio.shield(
                asyncio.to_thread(self.shared.store.release, self.shared.scope, owner, cooldown)
            )

    async def aclose(self) -> None:
        await self.inner.aclose()


class _CurrentProvider(TwelveDataProvider):
    @staticmethod
    def _parse_time_series(payload: Any, interval: BarInterval) -> TimeSeries:
        # Legacy parsing sorts bars. The current path must detect a malformed
        # original ordering before that normalization can conceal it.
        if isinstance(payload, dict) and isinstance(payload.get("values"), list):
            if len(payload["values"]) > 5000:
                raise MalformedProviderResponseError(
                    "Current history exceeds the documented response bound."
                )
            original = [
                TwelveDataProvider._parse_time_series(
                    {**payload, "values": [value]}, interval
                ).bars[0]
                for value in payload["values"]
            ]
            if any(
                a.timestamp >= b.timestamp for a, b in zip(original, original[1:], strict=False)
            ):
                raise MalformedProviderResponseError(
                    "Current history must be chronologically unique."
                )
        return TwelveDataProvider._parse_time_series(payload, interval)

    def _build_query(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
        provider_interval: str,
    ) -> dict[str, str]:
        params = super()._build_query(symbol, start, end, interval, provider_interval)
        if interval is BarInterval.ONE_DAY:
            # Hydration supplies inclusive exchange-local bounds. Twelve Data's
            # date-only upper bound is exclusive for daily history.
            params["end_date"] = (end.date() + timedelta(days=1)).isoformat()
        params.update(prepost="false", outputsize="5000", adjust="splits")
        return params


class CurrentDataGateway:
    """No raw client/provider is exposed to hydration or catalogue services."""

    def __init__(
        self,
        shared: SharedDispatcher,
        run: DispatchRun,
        api_key: str | None,
        transport: httpx.AsyncBaseTransport,
    ) -> None:
        self.run = run
        self._client = httpx.AsyncClient(
            base_url="https://api.twelvedata.com",
            follow_redirects=False,
            transport=_DispatchTransport(shared, run, transport),
        )
        # The dispatcher owns the run budget. It can reject a provider retry
        # before transport, which is distinct from exhausting provider retries.
        self._provider = _CurrentProvider(self._client, api_key)

    async def __aenter__(self) -> "CurrentDataGateway":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._client.__aexit__(*args)

    async def get_historical_bars(
        self, symbol: str, start: datetime, end: datetime, interval: BarInterval
    ) -> TimeSeries:
        if interval is not self.run.interval:
            raise DataUnavailable("run_interval_mismatch")
        try:
            timeout = self.run.remaining()
            result = await asyncio.wait_for(
                self._provider.get_historical_bars(symbol, start, end, interval),
                timeout=timeout,
            )
        except TimeoutError as error:
            raise DataUnavailable("deadline") from error
        self.run.remaining()
        return result

    async def list_stocks(self) -> tuple[SymbolMetadata, ...]:
        try:
            timeout = self.run.remaining()
            result = await asyncio.wait_for(self._provider.list_stocks(), timeout=timeout)
        except TimeoutError as error:
            raise DataUnavailable("deadline") from error
        self.run.remaining()
        return result
