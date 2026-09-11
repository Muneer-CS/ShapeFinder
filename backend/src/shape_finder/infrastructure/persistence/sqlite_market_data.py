import asyncio
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from shape_finder.core.errors import MalformedProviderResponseError
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries
from shape_finder.core.persistence import CoverageRange
from shape_finder.infrastructure.persistence.migrations import MIGRATIONS


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Persistence requires timezone-aware datetimes.")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


class SQLiteMarketDataRepository:
    """SQLite adapter with versioned schema setup and transactional batch upserts."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def initialize(self) -> None:
        await asyncio.to_thread(self._initialize_sync)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at_utc TEXT NOT NULL
                )
                """
            )
            applied = {
                int(row["version"])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for migration in MIGRATIONS:
                if migration.version in applied:
                    continue
                with connection:
                    for statement in migration.statements:
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at_utc) VALUES (?, ?)",
                        (migration.version, _utc_text(datetime.now(UTC))),
                    )
            connection.execute("PRAGMA optimize")

    async def get_time_series(
        self, symbol: str, interval: BarInterval, start: datetime, end: datetime
    ) -> TimeSeries:
        return await asyncio.to_thread(self._get_time_series_sync, symbol, interval, start, end)

    def _get_time_series_sync(
        self, symbol: str, interval: BarInterval, start: datetime, end: datetime
    ) -> TimeSeries:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT timestamp_utc, open, high, low, close, volume, timezone
                FROM market_bars
                WHERE symbol = ? AND interval = ?
                  AND timestamp_utc >= ? AND timestamp_utc <= ?
                ORDER BY timestamp_utc ASC
                """,
                (symbol.upper(), interval.value, _utc_text(start), _utc_text(end)),
            ).fetchall()

        timezone_name = str(rows[0]["timezone"]) if rows else "UTC"
        try:
            timezone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as error:
            raise MalformedProviderResponseError(
                "Stored market data has an invalid timezone."
            ) from error
        bars = tuple(
            PriceBar(
                timestamp=datetime.fromisoformat(str(row["timestamp_utc"])).astimezone(timezone),
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
            )
            for row in rows
        )
        return TimeSeries(
            symbol=symbol.upper(), interval=interval, timezone=timezone_name, bars=bars
        )

    async def get_coverage(
        self, symbol: str, interval: BarInterval, start: datetime, end: datetime
    ) -> Sequence[CoverageRange]:
        return await asyncio.to_thread(self._get_coverage_sync, symbol, interval, start, end)

    def _get_coverage_sync(
        self, symbol: str, interval: BarInterval, start: datetime, end: datetime
    ) -> tuple[CoverageRange, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT start_utc, end_utc, synced_at_utc
                FROM market_data_coverage
                WHERE symbol = ? AND interval = ?
                  AND end_utc >= ? AND start_utc <= ?
                ORDER BY start_utc ASC
                """,
                (symbol.upper(), interval.value, _utc_text(start), _utc_text(end)),
            ).fetchall()
        return tuple(
            CoverageRange(
                start=datetime.fromisoformat(str(row["start_utc"])),
                end=datetime.fromisoformat(str(row["end_utc"])),
                synced_at=datetime.fromisoformat(str(row["synced_at_utc"])),
            )
            for row in rows
        )

    async def upsert_time_series(
        self,
        series: Sequence[TimeSeries],
        coverage: Sequence[CoverageRange],
        *,
        source: str,
    ) -> None:
        await asyncio.to_thread(self._upsert_time_series_sync, series, coverage, source)

    def _upsert_time_series_sync(
        self,
        series: Sequence[TimeSeries],
        coverage: Sequence[CoverageRange],
        source: str,
    ) -> None:
        if len(series) != len(coverage):
            raise ValueError("Each synchronized series requires one coverage range.")
        with self._connect() as connection, connection:
            for item, covered in zip(series, coverage, strict=True):
                connection.executemany(
                    """
                    INSERT INTO market_bars(
                        symbol, interval, timestamp_utc, open, high, low, close, volume,
                        timezone, source, fetched_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, interval, timestamp_utc) DO UPDATE SET
                        open = excluded.open,
                        high = excluded.high,
                        low = excluded.low,
                        close = excluded.close,
                        volume = excluded.volume,
                        timezone = excluded.timezone,
                        source = excluded.source,
                        fetched_at_utc = excluded.fetched_at_utc
                    """,
                    [
                        (
                            item.symbol.upper(),
                            item.interval.value,
                            _utc_text(bar.timestamp),
                            str(bar.open),
                            str(bar.high),
                            str(bar.low),
                            str(bar.close),
                            str(bar.volume),
                            item.timezone,
                            source,
                            _utc_text(covered.synced_at),
                        )
                        for bar in item.bars
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO market_data_coverage(
                        symbol, interval, start_utc, end_utc, synced_at_utc, source
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, interval, start_utc, end_utc) DO UPDATE SET
                        synced_at_utc = excluded.synced_at_utc,
                        source = excluded.source
                    """,
                    (
                        item.symbol.upper(),
                        item.interval.value,
                        _utc_text(covered.start),
                        _utc_text(covered.end),
                        _utc_text(covered.synced_at),
                        source,
                    ),
                )
