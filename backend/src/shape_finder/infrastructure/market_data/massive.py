"""Massive's public API boundary for the scheduled daily refresh."""

import re
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from shape_finder.core.current_data import AccountPolicy, DataUnavailable
from shape_finder.core.universe import SymbolMetadata
from shape_finder.infrastructure.persistence.current_data_store import CurrentDataStore
from shape_finder.infrastructure.persistence.massive_store import DailyBar

HOST = "api.massive.com"
CATALOGUE = "/v3/reference/tickers"
EXCHANGES = {"XNAS": "NASDAQ", "XNYS": "NYSE"}
EASTERN = ZoneInfo("America/New_York")
SCOPE = "massive_stocks_basic"
TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,14}$")
RATE = AccountPolicy(attempts_per_60_seconds=5, provenance="Massive Stocks Basic request limit")


class MassiveClient:
    def __init__(
        self,
        client: httpx.Client,
        store: CurrentDataStore,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client, self.store = client, store
        self.clock, self.sleep = clock, sleep
        self.attempts = 0

    def _get(self, path: str, params: dict[str, str], operation: str) -> dict[str, Any]:
        owner = uuid4().hex
        while True:
            now = self.clock()
            try:
                self.store.reserve(SCOPE, owner, now, now + 60, operation, None, RATE)
            except DataUnavailable as error:
                if str(error) != "configured_rate_limit":
                    raise
                self.sleep(1)
            else:
                break
        cooldown = 0.0
        try:
            self.attempts += 1
            response = self.client.get(path, params=params)
            if response.status_code == 429:
                cooldown = self.clock() + 61
                raise DataUnavailable("massive_rate_limited")
            if response.status_code != 200:
                raise DataUnavailable(f"massive_http_{response.status_code}")
            body = response.json(parse_float=Decimal)
            if not isinstance(body, dict) or body.get("status") not in ("OK", "DELAYED"):
                raise DataUnavailable("massive_invalid_response")
            return body
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, DataUnavailable):
                raise
            raise DataUnavailable("massive_request_or_json_error") from None
        finally:
            self.store.release(SCOPE, owner, cooldown)

    def catalogue(self) -> tuple[SymbolMetadata, ...]:
        members: dict[str, SymbolMetadata] = {}
        for mic, exchange in EXCHANGES.items():
            params = {
                "exchange": mic,
                "market": "stocks",
                "active": "true",
                "type": "CS",
                "limit": "1000",
            }
            cursors: set[str] = set()
            total = 0
            while True:
                body = self._get(CATALOGUE, params, "catalogue")
                for row in _rows(body):
                    ticker, name = row.get("ticker"), row.get("name")
                    if (
                        not isinstance(ticker, str)
                        or not TICKER.fullmatch(ticker)
                        or not isinstance(name, str)
                        or not name.strip()
                        or row.get("primary_exchange") != mic
                        or row.get("type") != "CS"
                        or row.get("active") is not True
                        or row.get("market") != "stocks"
                        or row.get("locale") != "us"
                        or ticker in members
                    ):
                        raise DataUnavailable("massive_invalid_listing")
                    members[ticker] = SymbolMetadata(
                        ticker, name, exchange, "United States", "Common Stock", "USD"
                    )
                    total += 1
                next_url = body.get("next_url")
                if next_url is None:
                    break
                cursor = _catalogue_cursor(next_url)
                if cursor in cursors or len(cursors) >= 50:
                    raise DataUnavailable("massive_catalogue_pagination_invalid")
                cursors.add(cursor)
                params = {"cursor": cursor}
            if not total:
                raise DataUnavailable("massive_empty_exchange_catalogue")
        return tuple(members.values())

    def grouped(self, day: date) -> tuple[DailyBar, ...]:
        body = self._get(
            f"/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}",
            {"adjusted": "true", "include_otc": "false"},
            "grouped",
        )
        rows = _rows(body)
        if (
            not rows
            or body.get("adjusted") is not True
            or body.get("next_url")
            or body.get("resultsCount", len(rows)) != len(rows)
        ):
            raise DataUnavailable("massive_empty_or_incomplete_session")
        bars: dict[str, DailyBar] = {}
        for row in rows:
            try:
                ticker, stamp = row["T"], row["t"]
                if not isinstance(ticker, str) or not ticker or type(stamp) is not int:
                    raise ValueError
                if datetime.fromtimestamp(stamp / 1000, UTC).astimezone(EASTERN).date() != day:
                    raise ValueError
                values = tuple(row[k] for k in ("o", "h", "l", "c", "v"))
                if any(not isinstance(v, (int, Decimal)) or isinstance(v, bool) for v in values):
                    raise ValueError
                opened, high, low, closed, volume = (Decimal(v) for v in values)
                if (
                    not all(v.is_finite() for v in (opened, high, low, closed, volume))
                    or not 0 < low <= min(opened, closed) <= max(opened, closed) <= high
                    or volume < 0
                    or ticker in bars
                ):
                    raise ValueError
                bars[ticker] = DailyBar(ticker, opened, high, low, closed, volume)
            except (ArithmeticError, ValueError, KeyError, OSError):
                raise DataUnavailable("massive_invalid_session_bar") from None
        return tuple(bars.values())


def _rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    rows = body.get("results")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise DataUnavailable("massive_missing_results")
    return rows


def _catalogue_cursor(value: object) -> str:
    if isinstance(value, str):
        try:
            url = httpx.URL(value)
        except httpx.InvalidURL:
            pass
        else:
            if (
                url.scheme == "https"
                and url.host == HOST
                and url.port in (None, 443)
                and url.path == CATALOGUE
                and not url.userinfo
                and not url.fragment
            ):
                cursor = url.params.get("cursor")
                if isinstance(cursor, str) and cursor:
                    return cursor
    raise DataUnavailable("massive_catalogue_pagination_invalid")
