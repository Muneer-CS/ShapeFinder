import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from shape_finder.core.errors import (
    AuthenticationError,
    InvalidSymbolError,
    MalformedProviderResponseError,
    MissingApiKeyError,
    NoDataError,
    ProviderNetworkError,
    RateLimitError,
    UnsupportedIntervalError,
)
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.universe import SymbolMetadata

INTERVAL_MAP: Mapping[BarInterval, str] = {
    BarInterval.ONE_MINUTE: "1min",
    BarInterval.FIVE_MINUTES: "5min",
    BarInterval.FIFTEEN_MINUTES: "15min",
    BarInterval.THIRTY_MINUTES: "30min",
    BarInterval.ONE_HOUR: "1h",
    BarInterval.ONE_DAY: "1day",
}


class TwelveDataProvider:
    """Async Twelve Data adapter that returns only ShapeFinder domain models."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str | None,
        *,
        retry_attempts: int = 2,
        retry_backoff_seconds: float = 0.1,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._retry_attempts = max(1, retry_attempts)
        self._retry_backoff_seconds = retry_backoff_seconds

    async def get_historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> TimeSeries:
        if not self._api_key:
            raise MissingApiKeyError("Twelve Data is not configured.")
        try:
            provider_interval = INTERVAL_MAP[interval]
        except KeyError as error:
            raise UnsupportedIntervalError(f"Unsupported interval: {interval}") from error

        params = self._build_query(symbol, start, end, interval, provider_interval)
        payload = await self._request(params)
        return self._parse_time_series(payload, interval)

    async def list_stocks(self) -> tuple[SymbolMetadata, ...]:
        """Return normalized stock records from Twelve Data's official /stocks list."""
        if not self._api_key:
            raise MissingApiKeyError("Twelve Data is not configured.")
        payload = await self._request_endpoint(
            "/stocks",
            {
                "country": "United States",
                "type": "Common Stock",
                "apikey": self._api_key,
            },
        )
        return self._parse_stocks(payload)

    def _build_query(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
        provider_interval: str,
    ) -> dict[str, str]:
        if start.tzinfo is None or end.tzinfo is None:
            raise MalformedProviderResponseError("Provider received timezone-naive boundaries.")

        if interval is BarInterval.ONE_DAY:
            start_value = start.date().isoformat()
            end_value = end.date().isoformat()
        else:
            start_value = start.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")
            end_value = end.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="seconds")

        return {
            "symbol": symbol.upper(),
            "interval": provider_interval,
            "start_date": start_value,
            "end_date": end_value,
            "timezone": "UTC",
            "order": "ASC",
            "apikey": self._api_key or "",
        }

    async def _request(self, params: dict[str, str]) -> Any:
        return await self._request_endpoint("/time_series", params)

    async def _request_endpoint(self, endpoint: str, params: dict[str, str]) -> Any:
        for attempt in range(self._retry_attempts):
            try:
                response = await self._client.get(endpoint, params=params)
            except httpx.RequestError as error:
                if attempt + 1 == self._retry_attempts:
                    raise ProviderNetworkError("Market-data provider is unreachable.") from error
                await asyncio.sleep(self._retry_backoff_seconds * (attempt + 1))
                continue

            if response.status_code >= 500 and attempt + 1 < self._retry_attempts:
                await asyncio.sleep(self._retry_backoff_seconds * (attempt + 1))
                continue

            try:
                payload: Any = response.json()
            except ValueError as error:
                raise MalformedProviderResponseError(
                    "Market-data provider returned invalid JSON."
                ) from error

            self._raise_for_provider_error(response.status_code, payload)
            return payload

        raise ProviderNetworkError("Market-data provider request failed.")

    @staticmethod
    def _parse_stocks(payload: Any) -> tuple[SymbolMetadata, ...]:
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise MalformedProviderResponseError(
                "Provider stock catalog is missing its data array."
            )
        records: list[SymbolMetadata] = []
        try:
            for item in payload["data"]:
                if not isinstance(item, dict):
                    raise TypeError
                required = ("symbol", "name", "exchange", "country", "type", "currency")
                values = [item[field] for field in required]
                if not all(isinstance(value, str) and value.strip() for value in values):
                    raise TypeError
                raw_active = item.get("active", True)
                if not isinstance(raw_active, (bool, str, int)):
                    raise TypeError
                active = raw_active is True or str(raw_active).strip().lower() in {
                    "1",
                    "true",
                    "active",
                }
                records.append(
                    SymbolMetadata(
                        symbol=str(item["symbol"]).strip().upper(),
                        name=str(item["name"]).strip(),
                        exchange=str(item["exchange"]).strip().upper(),
                        country=str(item["country"]).strip(),
                        security_type=str(item["type"]).strip(),
                        currency=str(item["currency"]).strip().upper(),
                        active=active,
                    )
                )
        except (KeyError, TypeError) as error:
            raise MalformedProviderResponseError(
                "Provider returned malformed stock metadata."
            ) from error
        return tuple(records)

    @staticmethod
    def _raise_for_provider_error(status_code: int, payload: Any) -> None:
        if status_code < 400 and not (
            isinstance(payload, dict) and payload.get("status") == "error"
        ):
            return

        code = payload.get("code", status_code) if isinstance(payload, dict) else status_code
        message = str(payload.get("message", "")) if isinstance(payload, dict) else ""
        lowered = message.lower()
        if code in {401, 403} or "api key" in lowered or "apikey" in lowered:
            raise AuthenticationError("Market-data provider rejected its credentials.")
        if code == 429:
            raise RateLimitError("Market-data provider rate limit exceeded.")
        if "symbol" in lowered and any(term in lowered for term in ("invalid", "not found")):
            raise InvalidSymbolError("Unknown symbol or no supported instrument found.")
        if code == 400:
            raise InvalidSymbolError("The provider rejected the requested symbol or parameters.")
        raise ProviderNetworkError("Market-data provider request failed.")

    @staticmethod
    def _parse_time_series(payload: Any, interval: BarInterval) -> TimeSeries:
        if not isinstance(payload, dict):
            raise MalformedProviderResponseError("Expected a JSON object from the provider.")
        meta = payload.get("meta")
        values = payload.get("values")
        if not isinstance(meta, dict) or not isinstance(values, list):
            raise MalformedProviderResponseError("Provider response is missing metadata or values.")
        if not values:
            raise NoDataError("No market data exists for the requested period.")

        symbol = meta.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise MalformedProviderResponseError("Provider metadata is missing a symbol.")

        if interval is BarInterval.ONE_DAY:
            timezone_name = meta.get("exchange_timezone")
            if not isinstance(timezone_name, str) or not timezone_name:
                raise MalformedProviderResponseError(
                    "Provider metadata is missing exchange timezone."
                )
        else:
            timezone_name = "UTC"

        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as error:
            raise MalformedProviderResponseError(
                "Provider returned an unknown timezone."
            ) from error

        bars: list[PriceBar] = []
        try:
            for value in values:
                if not isinstance(value, dict):
                    raise TypeError
                timestamp = datetime.fromisoformat(str(value["datetime"]))
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone)
                bars.append(
                    PriceBar(
                        timestamp=timestamp,
                        open=Decimal(str(value["open"])),
                        high=Decimal(str(value["high"])),
                        low=Decimal(str(value["low"])),
                        close=Decimal(str(value["close"])),
                        volume=Decimal(str(value["volume"])),
                    )
                )
        except (KeyError, TypeError, ValueError, InvalidOperation) as error:
            raise MalformedProviderResponseError(
                "Provider returned malformed OHLCV data."
            ) from error

        return TimeSeries(
            symbol=symbol.upper(),
            interval=interval,
            timezone=timezone_name,
            bars=tuple(sorted(bars, key=lambda bar: bar.timestamp)),
        )
