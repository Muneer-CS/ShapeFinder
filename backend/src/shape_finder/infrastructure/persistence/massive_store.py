"""Provider-specific daily batches and atomically replaced listing snapshots."""

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, date, datetime
from decimal import Decimal, DecimalException
from pathlib import Path
from typing import NamedTuple

from shape_finder.core.universe import SymbolMetadata


class DailyBar(NamedTuple):
    symbol: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


DailyHistory = dict[date, tuple[DailyBar, datetime]]
HISTORY_BATCH_SIZE = 128


class MassiveStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def catalog_snapshot(self) -> tuple[tuple[SymbolMetadata, ...], datetime | None]:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN")
            stamp = db.execute("SELECT retrieved_at FROM massive_catalogue WHERE id=1").fetchone()
            rows = db.execute("SELECT symbol,name,exchange FROM massive_listings").fetchall()
        return (
            tuple(
                SymbolMetadata(s, n, e, "United States", "Common Stock", "USD") for s, n, e in rows
            ),
            datetime.fromisoformat(stamp[0]) if stamp else None,
        )

    def replace_catalog(self, members: Sequence[SymbolMetadata], retrieved: datetime) -> None:
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM massive_listings")
            db.executemany(
                "INSERT INTO massive_listings VALUES (?,?,?)",
                ((m.symbol, m.name, m.exchange) for m in members),
            )
            db.execute(
                "INSERT OR REPLACE INTO massive_catalogue VALUES (1,?)",
                (retrieved.astimezone(UTC).isoformat(),),
            )

    def completed(self) -> dict[date, datetime]:
        with closing(sqlite3.connect(self.path)) as db:
            return {
                date.fromisoformat(day): datetime.fromisoformat(retrieved)
                for day, retrieved in db.execute(
                    "SELECT session_date,retrieved_at FROM massive_sessions"
                )
            }

    def save_session(
        self, day: date, bars: Sequence[DailyBar], retrieved: datetime, eligible_at: datetime
    ) -> None:
        if not bars:
            raise ValueError("Cannot mark an empty Massive session complete.")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA foreign_keys=ON")
            stamp = day.isoformat()
            if retrieved < eligible_at:
                raise ValueError("Batch retrieval precedes its publication threshold.")
            changed = db.execute(
                """INSERT INTO massive_sessions VALUES (?,?)
                ON CONFLICT(session_date) DO UPDATE SET retrieved_at=excluded.retrieved_at
                WHERE massive_sessions.retrieved_at < ?""",
                (
                    stamp,
                    retrieved.astimezone(UTC).isoformat(),
                    eligible_at.astimezone(UTC).isoformat(),
                ),
            )
            if changed.rowcount:
                db.execute("DELETE FROM massive_daily_bars WHERE session_date=?", (stamp,))
                db.executemany(
                    "INSERT INTO massive_daily_bars VALUES (?,?,?,?,?,?,?)",
                    ((b.symbol, stamp, *map(str, b[1:])) for b in bars),
                )

    def histories(
        self, symbols: Sequence[str], start: date, end: date
    ) -> dict[str, DailyHistory | None]:
        """Read one batch. A malformed stored row invalidates only its own ticker."""
        if not 1 <= len(symbols) <= HISTORY_BATCH_SIZE:
            raise ValueError("Massive history batches require 1 to 128 symbols.")
        placeholders = ",".join("?" for _ in symbols)
        result: dict[str, DailyHistory | None] = {symbol: {} for symbol in symbols}
        with closing(sqlite3.connect(self.path)) as db:
            for symbol, day, o, h, low, c, v, retrieved in db.execute(
                f"""SELECT b.symbol, b.session_date, b.open, b.high, b.low, b.close,
                    b.volume, s.retrieved_at
                FROM massive_daily_bars b JOIN massive_sessions s USING(session_date)
                WHERE b.symbol IN ({placeholders}) AND b.session_date BETWEEN ? AND ?""",
                (*symbols, start.isoformat(), end.isoformat()),
            ):
                history = result[symbol]
                if history is None:
                    continue
                try:
                    history[date.fromisoformat(day)] = (
                        DailyBar(
                            symbol, Decimal(o), Decimal(h), Decimal(low), Decimal(c), Decimal(v)
                        ),
                        datetime.fromisoformat(retrieved),
                    )
                except (TypeError, ValueError, DecimalException):
                    result[symbol] = None
        return result

    def history(self, symbol: str, start: date, end: date) -> DailyHistory:
        history = self.histories((symbol,), start, end)[symbol]
        if history is None:
            raise ValueError("Malformed stored Massive history.")
        return history
