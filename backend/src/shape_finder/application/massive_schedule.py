"""Resume the daily refresh on the same service and volume as the scanner."""

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time

from shape_finder.application.massive_daily import required_sessions
from shape_finder.config import Settings
from shape_finder.core.current_data import DataUnavailable

logger = logging.getLogger(__name__)


async def refresh_if_due(
    settings: Settings, now: datetime, refresh: Callable[[], Awaitable[bool]]
) -> bool:
    session = required_sessions(now, 1, time.fromisoformat(settings.massive_publication_time_et))[
        0
    ].session_date.isoformat()
    stamp = settings.database_path.with_suffix(".massive-refresh-date")
    try:
        if stamp.read_text(encoding="ascii").strip() == session:
            return True
    except FileNotFoundError:
        pass
    if not await refresh():
        return False
    pending = stamp.with_suffix(".massive-refresh-date.tmp")
    pending.write_text(session + "\n", encoding="ascii")
    pending.replace(stamp)
    return True


async def _refresh() -> bool:
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-B", "-m", "scripts.refresh_massive_daily"
    )
    try:
        return await process.wait() == 0
    except asyncio.CancelledError:
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        await process.wait()
        raise


async def schedule_massive_refresh(settings: Settings) -> None:
    while True:
        try:
            complete = await refresh_if_due(settings, datetime.now(UTC), _refresh)
        except (DataUnavailable, OSError) as error:
            logger.warning("massive_refresh_scheduler outcome=%s", type(error).__name__)
            complete = False
        await asyncio.sleep(60 if complete else 30 * 60)
