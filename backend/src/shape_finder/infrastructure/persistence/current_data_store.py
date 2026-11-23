"""Short SQLite transactions for canonical observations and cross-process dispatch leases."""

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from shape_finder.core.current_data import AccountPolicy, CanonicalBar, DataUnavailable
from shape_finder.core.market_data import BarInterval
from shape_finder.core.universe import SymbolMetadata
from shape_finder.infrastructure.persistence.sqlite_market_data import SQLiteMarketDataRepository


class CurrentDataStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.repository = SQLiteMarketDataRepository(path)

    def _connect(self) -> sqlite3.Connection:
        # WAL/schema initialization belongs to initialize(), outside HTTP deadlines.
        # Short contention waits bound cancellation cleanup on this new data path.
        db = sqlite3.connect(self.path, timeout=0.1)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def initialize(self) -> None:
        self.repository._initialize_sync()

    def catalog_snapshot(self) -> tuple[tuple[SymbolMetadata, ...], datetime | None]:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN")
            rows = db.execute("SELECT * FROM universe_symbols ORDER BY symbol").fetchall()
            refreshed = db.execute("SELECT MAX(refreshed_at_utc) FROM universe_refresh").fetchone()[
                0
            ]
        members = tuple(
            SymbolMetadata(
                str(r["symbol"]),
                str(r["name"]),
                str(r["exchange"]),
                str(r["country"]),
                str(r["security_type"]),
                str(r["currency"]),
                bool(r["active"]),
            )
            for r in rows
        )
        return members, datetime.fromisoformat(str(refreshed)) if refreshed else None

    def save(self, symbol: str, bars: Sequence[CanonicalBar]) -> None:
        # Validate before entering the transaction, and commit an entire validated chunk.
        checked = [CanonicalBar.model_validate_json(b.model_dump_json()) for b in bars]
        with closing(self._connect()) as db, db:
            for bar in checked:
                db.execute(
                    """INSERT INTO current_candles VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(symbol,interval,open_at,policy_key)
                    DO UPDATE SET payload=excluded.payload""",
                    (
                        symbol,
                        bar.identity.interval.value,
                        bar.identity.open_at.astimezone(UTC).isoformat(),
                        bar.policy_key,
                        bar.model_dump_json(),
                    ),
                )

    def load(self, symbol: str, interval: BarInterval, policy_key: str) -> tuple[CanonicalBar, ...]:
        with closing(self._connect()) as db:
            rows = db.execute(
                """SELECT payload FROM current_candles
                WHERE symbol=? AND interval=? AND policy_key=? ORDER BY open_at""",
                (symbol, interval.value, policy_key),
            ).fetchall()
        return tuple(CanonicalBar.model_validate_json(row[0]) for row in rows)

    def reserve(
        self,
        scope: str,
        owner: str,
        now: float,
        until: float,
        endpoint: str,
        cost: int | None,
        policy: AccountPolicy | None,
    ) -> None:
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO provider_coordination(account_scope) VALUES (?)", (scope,)
            )
            state = db.execute(
                "SELECT * FROM provider_coordination WHERE account_scope=?", (scope,)
            ).fetchone()
            assert state is not None
            if state["cooldown_until"] > now:
                raise DataUnavailable("provider_cooldown")
            if state["lease_until"] > now:
                raise DataUnavailable("provider_busy")
            if policy is not None:
                recent = db.execute(
                    """SELECT started_at,credit_cost FROM provider_attempts
                    WHERE account_scope=? AND started_at>?""",
                    (scope, now - 86400),
                ).fetchall()
                if (
                    policy.attempts_per_60_seconds is not None
                    and sum(row["started_at"] > now - 60 for row in recent)
                    >= policy.attempts_per_60_seconds
                ):
                    raise DataUnavailable("configured_rate_limit")
                if policy.credits_per_86400_seconds is not None:
                    if cost is None or any(row["credit_cost"] is None for row in recent):
                        raise DataUnavailable("credit_cost_unknown")
                    if (
                        sum(int(row["credit_cost"]) for row in recent) + cost
                        > policy.credits_per_86400_seconds
                    ):
                        raise DataUnavailable("configured_credit_limit")
            db.execute(
                "UPDATE provider_coordination SET owner=?,lease_until=? WHERE account_scope=?",
                (owner, until, scope),
            )
            db.execute(
                "INSERT INTO provider_attempts(account_scope,started_at,endpoint,credit_cost) "
                "VALUES(?,?,?,?)",
                (scope, now, endpoint, cost),
            )
            # Keep only the local policy window; no credentials or request URLs are stored.
            db.execute("DELETE FROM provider_attempts WHERE started_at<=?", (now - 86400,))

    def release(self, scope: str, owner: str, cooldown_until: float = 0) -> None:
        with closing(self._connect()) as db, db:
            db.execute(
                """UPDATE provider_coordination SET owner=NULL,lease_until=0,
                cooldown_until=MAX(cooldown_until,?) WHERE account_scope=? AND owner=?""",
                (cooldown_until, scope, owner),
            )

    def usage(self, scope: str) -> tuple[int, int, int]:
        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT COUNT(*),COALESCE(SUM(credit_cost),0),
                COALESCE(SUM(credit_cost IS NULL),0) FROM provider_attempts
                WHERE account_scope=?""",
                (scope,),
            ).fetchone()
        return int(row[0]), int(row[1]), int(row[2])
