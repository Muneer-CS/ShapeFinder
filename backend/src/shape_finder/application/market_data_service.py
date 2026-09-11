from datetime import datetime

from shape_finder.core.errors import InvalidDateRangeError
from shape_finder.core.market_data import BarInterval, MarketDataProvider, TimeSeries


class MarketDataService:
    def __init__(self, provider: MarketDataProvider) -> None:
        self._provider = provider

    async def get_time_series(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        interval: BarInterval,
    ) -> TimeSeries:
        if start.tzinfo is None or end.tzinfo is None:
            raise InvalidDateRangeError("Start and end timestamps must include a timezone.")
        if start >= end:
            raise InvalidDateRangeError("Start must be earlier than end.")

        return await self._provider.get_historical_bars(
            symbol=symbol.upper(),
            start=start,
            end=end,
            interval=interval,
        )
