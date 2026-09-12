from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class BarInterval(StrEnum):
    ONE_MINUTE = "1min"
    FIVE_MINUTES = "5min"
    FIFTEEN_MINUTES = "15min"
    THIRTY_MINUTES = "30min"
    ONE_HOUR = "1h"
    ONE_DAY = "1day"
    ONE_WEEK = "1week"


@dataclass(frozen=True, slots=True)
class PriceBar:
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True, slots=True)
class TimeSeries:
    symbol: str
    interval: BarInterval
    timezone: str
    bars: tuple[PriceBar, ...]


def validate_time_series(series: TimeSeries) -> None:
    """Validate the provider/persistence boundary before data can enter the cache."""
    if not series.symbol.strip() or not series.timezone.strip():
        raise ValueError("Market-data identity is incomplete.")
    try:
        ZoneInfo(series.timezone)
    except ZoneInfoNotFoundError as error:
        raise ValueError("Market-data timezone is invalid.") from error
    seen: set[datetime] = set()
    for bar in series.bars:
        if bar.timestamp.tzinfo is None or bar.timestamp in seen:
            raise ValueError("Market-data timestamps must be unique and timezone-aware.")
        seen.add(bar.timestamp)
        prices = (bar.open, bar.high, bar.low, bar.close)
        if any(not value.is_finite() or value <= 0 for value in prices):
            raise ValueError("Market-data prices must be finite and positive.")
        if not bar.volume.is_finite() or bar.volume < 0:
            raise ValueError("Market-data volume must be finite and non-negative.")
        if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close):
            raise ValueError("Market-data OHLC values are inconsistent.")


class MarketDataProvider(Protocol):
    """Contract implemented by Twelve Data or any future market-data adapter."""

    async def get_historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> TimeSeries: ...
