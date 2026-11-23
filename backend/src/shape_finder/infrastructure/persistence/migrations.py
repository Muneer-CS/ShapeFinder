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
    Migration(
        version=3,
        statements=(
            """
            CREATE TABLE hydration_failures (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                start_utc TEXT NOT NULL,
                end_utc TEXT NOT NULL,
                category TEXT NOT NULL,
                failed_at_utc TEXT NOT NULL,
                retry_after_utc TEXT NOT NULL,
                PRIMARY KEY (symbol, interval, start_utc, end_utc)
            ) WITHOUT ROWID
            """,
            """
            CREATE INDEX hydration_failures_retry_idx
            ON hydration_failures(retry_after_utc)
            """,
        ),
    ),
    Migration(
        version=4,
        statements=(
            """CREATE TABLE current_candles (
                symbol TEXT NOT NULL, interval TEXT NOT NULL, open_at TEXT NOT NULL,
                policy_key TEXT NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(symbol, interval, open_at, policy_key)
            ) WITHOUT ROWID""",
            """CREATE TABLE provider_coordination (
                account_scope TEXT PRIMARY KEY, owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
                cooldown_until REAL NOT NULL DEFAULT 0
            ) WITHOUT ROWID""",
            """CREATE TABLE provider_attempts (
                id INTEGER PRIMARY KEY, account_scope TEXT NOT NULL, started_at REAL NOT NULL,
                endpoint TEXT NOT NULL, credit_cost INTEGER
            )""",
            """CREATE INDEX provider_attempts_scope_time
                ON provider_attempts(account_scope, started_at)""",
        ),
    ),
    Migration(
        version=5,
        statements=(
            """CREATE TABLE massive_listings (
                symbol TEXT PRIMARY KEY, name TEXT NOT NULL, exchange TEXT NOT NULL
            ) WITHOUT ROWID""",
            """CREATE TABLE massive_catalogue (
                id INTEGER PRIMARY KEY CHECK(id=1), retrieved_at TEXT NOT NULL
            )""",
            """CREATE TABLE massive_sessions (
                session_date TEXT PRIMARY KEY, retrieved_at TEXT NOT NULL
            ) WITHOUT ROWID""",
            """CREATE TABLE massive_daily_bars (
                symbol TEXT NOT NULL, session_date TEXT NOT NULL REFERENCES massive_sessions,
                open TEXT NOT NULL, high TEXT NOT NULL, low TEXT NOT NULL,
                close TEXT NOT NULL, volume TEXT NOT NULL,
                PRIMARY KEY(symbol, session_date)
            ) WITHOUT ROWID""",
            "CREATE INDEX massive_daily_bars_session ON massive_daily_bars(session_date)",
        ),
    ),
)
