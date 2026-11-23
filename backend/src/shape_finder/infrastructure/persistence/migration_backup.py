"""WAL-safe, fail-closed protection before upgrading an existing pre-v4 database."""

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def backup_before_v4(path: Path) -> Path | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    with closing(sqlite3.connect(path)) as source:
        source.execute("BEGIN")  # Pin the pre-upgrade read snapshot, including committed WAL.
        exists = source.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='schema_migrations'"
        ).fetchone()
        applied = (
            {int(row[0]) for row in source.execute("SELECT version FROM schema_migrations")}
            if exists
            else set()
        )
        if 4 in applied or any(v > 4 for v in applied):
            return None
        folder = path.parent / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        destination = folder / f"{path.stem}.pre-v4.{stamp}.{uuid4().hex}.sqlite3"
        destination.touch(exist_ok=False)
        # SQLite's backup API includes committed WAL content. Never copy only the main file.
        with closing(sqlite3.connect(destination)) as target:
            source.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Pre-migration backup integrity check failed.")
        return destination
