"""Refresh the Massive daily cache using the configured ShapeFinder database."""

import argparse
import json
import logging
import sqlite3
from datetime import UTC, datetime, time, timedelta

import httpx

from shape_finder.application.massive_daily import refresh_daily
from shape_finder.config import Settings
from shape_finder.core.current_data import DataUnavailable
from shape_finder.infrastructure.market_data.massive import HOST, MassiveClient
from shape_finder.infrastructure.persistence.current_data_store import CurrentDataStore
from shape_finder.infrastructure.persistence.massive_store import MassiveStore


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-catalogue", action="store_true")
    args = parser.parse_args()
    settings = Settings()
    key = settings.massive_api_key.get_secret_value().strip() if settings.massive_api_key else ""
    if not key:
        parser.exit(2, "MASSIVE_API_KEY is missing from the process environment.\n")
    store = CurrentDataStore(settings.database_path)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    adapter: MassiveClient | None = None
    try:
        store.initialize()
        with httpx.Client(
            base_url=f"https://{HOST}",
            headers={"Authorization": f"Bearer {key}"},
            transport=httpx.HTTPTransport(retries=0, trust_env=False),
            timeout=httpx.Timeout(settings.market_data_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            adapter = MassiveClient(client, store)
            result = refresh_daily(
                MassiveStore(store.path),
                adapter,
                datetime.now(UTC),
                settings.massive_backfill_sessions,
                time.fromisoformat(settings.massive_publication_time_et),
                timedelta(hours=settings.universe_ttl_hours),
                force_catalogue=args.refresh_catalogue,
            )
    except (DataUnavailable, sqlite3.Error) as error:
        reason = str(error) if isinstance(error, DataUnavailable) else "sqlite_error"
        print(json.dumps({"error": reason, "http_attempts": adapter.attempts if adapter else 0}))
        return 2
    print(json.dumps(result, indent=2))
    return 2 if result["pending_sessions"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
