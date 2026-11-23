"""Already-rejected paths must not even construct inner coroutines or schedule tasks."""

import asyncio
import gc
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from shape_finder.application.current_history import CurrentHistoryDataService
from shape_finder.core.current_data import DataUnavailable
from shape_finder.core.market_data import BarInterval
from shape_finder.infrastructure.market_data.current_dispatch import CurrentDataGateway, DispatchRun


@pytest.mark.parametrize("reason", ["cancelled", "deadline"])
@pytest.mark.parametrize("site", ["history", "provider_history", "provider_catalogue"])
def test_rejected_wait_does_not_construct_or_schedule_work(
    monkeypatch: pytest.MonkeyPatch,
    recwarn: pytest.WarningsRecorder,
    reason: str,
    site: str,
) -> None:
    run = (
        DispatchRun(BarInterval.ONE_DAY, started=0)
        if reason == "deadline"
        else DispatchRun(BarInterval.ONE_DAY)
    )
    if reason == "cancelled":
        run.cancellation.set()
    factory = Mock(side_effect=AssertionError("Guard must run before coroutine construction"))
    scheduler = Mock(side_effect=AssertionError("Rejected operation must not schedule work"))
    monkeypatch.setattr(asyncio, "wait_for", scheduler)
    monkeypatch.setattr(asyncio, "create_task", scheduler)
    if site == "history":
        service = CurrentHistoryDataService(Mock(), SimpleNamespace(run=run))
        monkeypatch.setattr(service, "_load", factory)
        operation = service.load(Mock())
    else:
        # Only the guard is exercised: no HTTP client, connection or event loop is created.
        gateway = object.__new__(CurrentDataGateway)
        gateway.run = run
        gateway._provider = SimpleNamespace(get_historical_bars=factory, list_stocks=factory)
        operation = (
            gateway.list_stocks()
            if site == "provider_catalogue"
            else gateway.get_historical_bars(
                "AAA",
                datetime(2025, 1, 1, tzinfo=UTC),
                datetime(2025, 1, 2, tzinfo=UTC),
                BarInterval.ONE_DAY,
            )
        )
    try:
        # Completion before the first suspension is the contract for these rejected paths.
        with pytest.raises(DataUnavailable, match=f"^{reason}$"):
            operation.send(None)
    finally:
        operation.close()
    gc.collect()
    factory.assert_not_called()
    scheduler.assert_not_called()
    assert run.attempts == 0
    assert not [str(w.message) for w in recwarn if issubclass(w.category, RuntimeWarning)]
