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
)
