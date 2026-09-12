from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    statements: tuple[str, ...]


MIGRATIONS = (
    Migration(
        version=1,
        statements=(
            """
            CREATE TABLE market_bars (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                timestamp_utc TEXT NOT NULL,
                open TEXT NOT NULL,
                high TEXT NOT NULL,
                low TEXT NOT NULL,
                close TEXT NOT NULL,
                volume TEXT NOT NULL,
                timezone TEXT NOT NULL,
                source TEXT NOT NULL,
                fetched_at_utc TEXT NOT NULL,
                PRIMARY KEY (symbol, interval, timestamp_utc)
            ) WITHOUT ROWID
            """,
            """
            CREATE TABLE market_data_coverage (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                start_utc TEXT NOT NULL,
                end_utc TEXT NOT NULL,
                synced_at_utc TEXT NOT NULL,
                source TEXT NOT NULL,
                PRIMARY KEY (symbol, interval, start_utc, end_utc),
                CHECK (start_utc <= end_utc)
            ) WITHOUT ROWID
            """,
        ),
    ),
    Migration(
        version=2,
        statements=(
            """
            CREATE TABLE universe_symbols (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                exchange TEXT NOT NULL,
                country TEXT NOT NULL,
                security_type TEXT NOT NULL,
                currency TEXT NOT NULL,
                active INTEGER NOT NULL CHECK (active IN (0, 1)),
                source TEXT NOT NULL,
                refreshed_at_utc TEXT NOT NULL
            ) WITHOUT ROWID
            """,
            """
            CREATE INDEX universe_symbols_exchange_idx
            ON universe_symbols(exchange, active, security_type, country)
            """,
            """
            CREATE TABLE universe_refresh (
                source TEXT PRIMARY KEY,
                refreshed_at_utc TEXT NOT NULL,
                symbol_count INTEGER NOT NULL CHECK (symbol_count >= 0)
            ) WITHOUT ROWID
            """,
        ),
    ),
)
