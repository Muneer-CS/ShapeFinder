import asyncio
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from shape_finder.core.errors import CacheError, MalformedProviderResponseError
from shape_finder.core.market_data import BarInterval, PriceBar, TimeSeries, validate_time_series
from shape_finder.core.persistence import CoverageRange, HydrationFailureRecord
from shape_finder.core.universe import SymbolMetadata
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
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _initialize_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
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
            expected_versions = tuple(range(1, len(MIGRATIONS) + 1))
            migration_versions = tuple(migration.version for migration in MIGRATIONS)
            if migration_versions != expected_versions:
                raise RuntimeError("Application migrations are not a contiguous version sequence.")
            unknown = applied - set(migration_versions)
            if unknown:
                raise RuntimeError("Database schema is newer than this application supports.")
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

    async def check_readiness(self) -> None:
        await asyncio.to_thread(self._check_readiness_sync)

    def _check_readiness_sync(self) -> None:
        with closing(self._connect()) as connection:
            integrity = connection.execute("PRAGMA quick_check(1)").fetchone()
            if integrity is None or str(integrity[0]).lower() != "ok":
                raise RuntimeError("Database integrity check failed.")
            applied = {
                int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")
            }
            expected = {migration.version for migration in MIGRATIONS}
            if applied != expected:
                raise RuntimeError("Database migrations are not current.")

    async def get_time_series(
        self, symbol: str, interval: BarInterval, start: datetime, end: datetime
    ) -> TimeSeries:
        return await asyncio.to_thread(self._get_time_series_sync, symbol, interval, start, end)

    def _get_time_series_sync(
        self, symbol: str, interval: BarInterval, start: datetime, end: datetime
    ) -> TimeSeries:
        with closing(self._connect()) as connection:
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
        with closing(self._connect()) as connection:
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
        try:
            await asyncio.to_thread(self._upsert_time_series_sync, series, coverage, source)
        except sqlite3.Error as error:
            raise CacheError("Market-data cache write failed.") from error

    def _upsert_time_series_sync(
        self,
        series: Sequence[TimeSeries],
        coverage: Sequence[CoverageRange],
        source: str,
    ) -> None:
        if len(series) != len(coverage):
            raise ValueError("Each synchronized series requires one coverage range.")
        for item in series:
            validate_time_series(item)
        with closing(self._connect()) as connection, connection:
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

    async def replace_universe(
        self,
        symbols: Sequence[SymbolMetadata],
        *,
        refreshed_at: datetime,
        source: str,
    ) -> None:
        await asyncio.to_thread(self._replace_universe_sync, symbols, refreshed_at, source)

    def _replace_universe_sync(
        self,
        symbols: Sequence[SymbolMetadata],
        refreshed_at: datetime,
        source: str,
    ) -> None:
        refreshed_text = _utc_text(refreshed_at)
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM universe_symbols")
            connection.executemany(
                """
                INSERT INTO universe_symbols(
                    symbol, name, exchange, country, security_type, currency,
                    active, source, refreshed_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        item.symbol.upper(),
                        item.name,
                        item.exchange.upper(),
                        item.country,
                        item.security_type,
                        item.currency.upper(),
                        int(item.active),
                        source,
                        refreshed_text,
                    )
                    for item in symbols
                ],
            )
            connection.execute(
                """
                INSERT INTO universe_refresh(source, refreshed_at_utc, symbol_count)
                VALUES (?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    refreshed_at_utc = excluded.refreshed_at_utc,
                    symbol_count = excluded.symbol_count
                """,
                (source, refreshed_text, len(symbols)),
            )

    async def list_universe_symbols(self) -> Sequence[SymbolMetadata]:
        return await asyncio.to_thread(self._list_universe_symbols_sync)

    def _list_universe_symbols_sync(self) -> tuple[SymbolMetadata, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT symbol, name, exchange, country, security_type, currency, active
                FROM universe_symbols
                ORDER BY symbol ASC
                """
            ).fetchall()
        return tuple(
            SymbolMetadata(
                symbol=str(row["symbol"]),
                name=str(row["name"]),
                exchange=str(row["exchange"]),
                country=str(row["country"]),
                security_type=str(row["security_type"]),
                currency=str(row["currency"]),
                active=bool(row["active"]),
            )
            for row in rows
        )

    async def get_universe_refreshed_at(self) -> datetime | None:
        return await asyncio.to_thread(self._get_universe_refreshed_at_sync)

    def _get_universe_refreshed_at_sync(self) -> datetime | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT MAX(refreshed_at_utc) AS refreshed_at FROM universe_refresh"
            ).fetchone()
        value = row["refreshed_at"] if row else None
        return datetime.fromisoformat(str(value)) if value else None

    async def get_scan_ready_symbols(
        self,
        symbols: Sequence[str],
        interval: BarInterval,
        start: datetime,
        end: datetime,
        minimum_bars: int,
    ) -> Sequence[str]:
        return await asyncio.to_thread(
            self._get_scan_ready_symbols_sync,
            symbols,
            interval,
            start,
            end,
            minimum_bars,
        )

    async def get_suppressed_hydration_symbols(
        self,
        symbols: Sequence[str],
        interval: BarInterval,
        start: datetime,
        end: datetime,
        now: datetime,
    ) -> Sequence[str]:
        return await asyncio.to_thread(
            self._get_suppressed_hydration_symbols_sync,
            symbols,
            interval,
            start,
            end,
            now,
        )

    def _get_suppressed_hydration_symbols_sync(
        self,
        symbols: Sequence[str],
        interval: BarInterval,
        start: datetime,
        end: datetime,
        now: datetime,
    ) -> tuple[str, ...]:
        wanted = tuple(dict.fromkeys(symbol.upper() for symbol in symbols))
        if not wanted:
            return ()
        found: set[str] = set()
        requested_start = start.astimezone(UTC)
        requested_end = end.astimezone(UTC)
        with closing(self._connect()) as connection:
            for offset in range(0, len(wanted), 400):
                batch = wanted[offset : offset + 400]
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"""
                    SELECT symbol, start_utc, end_utc, category
                    FROM hydration_failures
                    WHERE symbol IN ({placeholders}) AND interval = ?
                      AND retry_after_utc > ?
                    """,
                    (
                        *batch,
                        interval.value,
                        _utc_text(now),
                    ),
                ).fetchall()
                for row in rows:
                    category = str(row["category"])
                    recorded_start = datetime.fromisoformat(str(row["start_utc"])).astimezone(UTC)
                    recorded_end = datetime.fromisoformat(str(row["end_utc"])).astimezone(UTC)
                    exact_range = (
                        recorded_start == requested_start and recorded_end == requested_end
                    )
                    permanent_symbol_failure = category in {
                        "unsupported_symbol",
                        "provider_rejected",
                    }
                    overlapping_no_data = category == "no_data" and _substantially_overlaps(
                        requested_start,
                        requested_end,
                        recorded_start,
                        recorded_end,
                    )
                    if exact_range or permanent_symbol_failure or overlapping_no_data:
                        found.add(str(row["symbol"]))
        return tuple(sorted(found))

    async def get_partial_coverage_symbols(
        self,
        symbols: Sequence[str],
        interval: BarInterval,
        start: datetime,
        end: datetime,
    ) -> Sequence[str]:
        return await asyncio.to_thread(
            self._get_partial_coverage_symbols_sync, symbols, interval, start, end
        )

    def _get_partial_coverage_symbols_sync(
        self,
        symbols: Sequence[str],
        interval: BarInterval,
        start: datetime,
        end: datetime,
    ) -> tuple[str, ...]:
        wanted = tuple(dict.fromkeys(symbol.upper() for symbol in symbols))
        if not wanted:
            return ()
        found: set[str] = set()
        with closing(self._connect()) as connection:
            for offset in range(0, len(wanted), 400):
                batch = wanted[offset : offset + 400]
                placeholders = ",".join("?" for _ in batch)
                rows = connection.execute(
                    f"""
                    SELECT DISTINCT symbol FROM market_data_coverage
                    WHERE symbol IN ({placeholders}) AND interval = ?
                      AND end_utc >= ? AND start_utc <= ?
                    """,
                    (*batch, interval.value, _utc_text(start), _utc_text(end)),
                ).fetchall()
                found.update(str(row["symbol"]) for row in rows)
        return tuple(sorted(found))

    async def record_hydration_failure(self, record: HydrationFailureRecord) -> None:
        await asyncio.to_thread(self._record_hydration_failure_sync, record)

    def _record_hydration_failure_sync(self, record: HydrationFailureRecord) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO hydration_failures(
                    symbol, interval, start_utc, end_utc, category,
                    failed_at_utc, retry_after_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, interval, start_utc, end_utc) DO UPDATE SET
                    category = excluded.category,
                    failed_at_utc = excluded.failed_at_utc,
                    retry_after_utc = excluded.retry_after_utc
                """,
                (
                    record.symbol.upper(),
                    record.interval.value,
                    _utc_text(record.start),
                    _utc_text(record.end),
                    record.category,
                    _utc_text(record.failed_at),
                    _utc_text(record.retry_after),
                ),
            )

    async def clear_hydration_failure(
        self, symbol: str, interval: BarInterval, start: datetime, end: datetime
    ) -> None:
        await asyncio.to_thread(self._clear_hydration_failure_sync, symbol, interval, start, end)

    def _clear_hydration_failure_sync(
        self, symbol: str, interval: BarInterval, start: datetime, end: datetime
    ) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """
                DELETE FROM hydration_failures
                WHERE symbol = ? AND interval = ? AND start_utc = ? AND end_utc = ?
                """,
                (symbol.upper(), interval.value, _utc_text(start), _utc_text(end)),
            )

    def _get_scan_ready_symbols_sync(
        self,
        symbols: Sequence[str],
        interval: BarInterval,
        start: datetime,
        end: datetime,
        minimum_bars: int,
    ) -> tuple[str, ...]:
        wanted = tuple(dict.fromkeys(symbol.upper() for symbol in symbols))
        if not wanted:
            return ()
        start_text, end_text = _utc_text(start), _utc_text(end)
        coverage: dict[str, list[tuple[str, str]]] = {symbol: [] for symbol in wanted}
        counts: dict[str, int] = {}
        with closing(self._connect()) as connection:
            for offset in range(0, len(wanted), 400):
                batch = wanted[offset : offset + 400]
                placeholders = ",".join("?" for _ in batch)
                coverage_rows = connection.execute(
                    f"""
                    SELECT symbol, start_utc, end_utc
                    FROM market_data_coverage
                    WHERE symbol IN ({placeholders}) AND interval = ?
                      AND end_utc >= ? AND start_utc <= ?
                    ORDER BY symbol, start_utc
                    """,
                    (*batch, interval.value, start_text, end_text),
                ).fetchall()
                for row in coverage_rows:
                    coverage[str(row["symbol"])].append(
                        (str(row["start_utc"]), str(row["end_utc"]))
                    )
                count_rows = connection.execute(
                    f"""
                    SELECT symbol, COUNT(*) AS bar_count
                    FROM market_bars
                    WHERE symbol IN ({placeholders}) AND interval = ?
                      AND timestamp_utc >= ? AND timestamp_utc <= ?
                    GROUP BY symbol
                    """,
                    (*batch, interval.value, start_text, end_text),
                ).fetchall()
                counts.update({str(row["symbol"]): int(row["bar_count"]) for row in count_rows})
        return tuple(
            symbol
            for symbol in wanted
            if counts.get(symbol, 0) >= minimum_bars
            and _coverage_contains(coverage[symbol], start_text, end_text, interval)
        )


def _coverage_contains(
    ranges: Sequence[tuple[str, str]], start: str, end: str, interval: BarInterval
) -> bool:
    requested_start = datetime.fromisoformat(start)
    requested_end = datetime.fromisoformat(end)
    cursor = requested_start
    steps = {
        BarInterval.ONE_MINUTE: timedelta(minutes=1),
        BarInterval.FIVE_MINUTES: timedelta(minutes=5),
        BarInterval.FIFTEEN_MINUTES: timedelta(minutes=15),
        BarInterval.THIRTY_MINUTES: timedelta(minutes=30),
        BarInterval.ONE_HOUR: timedelta(hours=1),
        BarInterval.ONE_DAY: timedelta(days=1),
        BarInterval.ONE_WEEK: timedelta(weeks=1),
    }
    for index, (range_start, range_end) in enumerate(ranges):
        range_start_at = datetime.fromisoformat(range_start)
        range_end_at = datetime.fromisoformat(range_end)
        allowed_start = cursor + steps[interval]
        starts_after_gap = (
            range_start_at >= allowed_start if index == 0 else range_start_at > allowed_start
        )
        if starts_after_gap:
            return False
        if range_end_at >= requested_end:
            return True
        if range_end_at > cursor:
            cursor = range_end_at
    return False


def _substantially_overlaps(
    first_start: datetime,
    first_end: datetime,
    second_start: datetime,
    second_end: datetime,
) -> bool:
    overlap = min(first_end, second_end) - max(first_start, second_start)
    if overlap <= timedelta(0):
        return False
    shorter = min(first_end - first_start, second_end - second_start)
    return shorter > timedelta(0) and overlap / shorter >= 0.5
