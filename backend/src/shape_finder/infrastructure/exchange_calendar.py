"""Pinned exchange_calendars adapter; listings, never ticker spelling, select calendars."""

from datetime import UTC, date, datetime, timedelta
from importlib.metadata import version
from typing import Any
from zoneinfo import ZoneInfo

import exchange_calendars as xcals  # type: ignore[import-untyped]

from shape_finder.core.candle_eligibility import CandleIdentity, ExchangeSession
from shape_finder.core.current_data import DataUnavailable
from shape_finder.core.market_data import BarInterval
from shape_finder.core.universe import SymbolMetadata

_MAPPINGS = {"NYSE": "XNYS", "XNYS": "XNYS", "NASDAQ": "NASDAQ", "XNAS": "XNAS"}
_STEPS = {
    BarInterval.ONE_MINUTE: 1,
    BarInterval.FIVE_MINUTES: 5,
    BarInterval.FIFTEEN_MINUTES: 15,
    BarInterval.THIRTY_MINUTES: 30,
    BarInterval.ONE_HOUR: 60,
}


class ListingCalendar:
    """Explicit bounded operational window; no promise of an indefinitely valid calendar."""

    def __init__(self, listing: SymbolMetadata, start: date, end: date) -> None:
        if (
            listing.country.upper() not in {"US", "USA", "UNITED STATES"}
            or not listing.active
            or listing.security_type != "Common Stock"
            or listing.exchange.upper() not in _MAPPINGS
        ):
            raise DataUnavailable("exchange_mapping_unavailable")
        if not date(2000, 1, 1) <= start < end <= date(2030, 12, 31):
            raise DataUnavailable("calendar_out_of_supported_window")
        cal: Any = xcals.get_calendar(
            _MAPPINGS[listing.exchange.upper()], start=start.isoformat(), end=end.isoformat()
        )
        self.calendar_id = str(cal.name)  # NASDAQ/XNAS are upstream aliases for XNYS.
        self.symbol = listing.symbol
        self.version = f"exchange_calendars:{version('exchange_calendars')}:{self.calendar_id}"
        self.timezone = str(cal.tz)
        self.supported_start, self.supported_end = start, end
        self._sessions = tuple(
            ExchangeSession(
                calendar_id=self.calendar_id,
                session_date=index.date(),
                timezone=self.timezone,
                open_at=row["open"].to_pydatetime(),
                close_at=row["close"].to_pydatetime(),
            )
            for index, row in cal.schedule.iterrows()
        )

    def sessions_through(self, cutoff: datetime) -> tuple[ExchangeSession, ...]:
        day = cutoff.astimezone(ZoneInfo(self.timezone)).date()
        return tuple(s for s in self._sessions if s.session_date <= day)

    def candles(self, interval: BarInterval) -> tuple[CandleIdentity, ...]:
        if interval not in (*_STEPS, BarInterval.ONE_DAY):
            raise DataUnavailable("interval_unavailable")
        result: list[CandleIdentity] = []
        for session in self._sessions:
            opened, closed = session.open_at.astimezone(UTC), session.close_at.astimezone(UTC)
            step = (
                closed - opened
                if interval is BarInterval.ONE_DAY
                else timedelta(minutes=_STEPS[interval])
            )
            while opened < closed:
                end = min(opened + step, closed)
                result.append(
                    CandleIdentity(
                        interval=interval,
                        calendar_id=self.calendar_id,
                        session_date=session.session_date,
                        timezone=self.timezone,
                        open_at=opened,
                        close_at=end,
                    )
                )
                if len(result) > 5000:
                    raise DataUnavailable("calendar_window_too_large")
                opened = end
        return tuple(result)
