from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class BarInterval(StrEnum):
    ONE_MINUTE = "1min"
    FIVE_MINUTES = "5min"
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


class MarketDataProvider(Protocol):
    """Contract implemented by Twelve Data or any future market-data adapter."""

    async def get_historical_bars(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> Sequence[PriceBar]: ...
