from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from shape_finder.core.candle_eligibility import (
    CachedCandle,
    ExchangeSession,
    FrozenCutoff,
    ProviderCapability,
    resolve_eligibility,
)


def instant(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def session(day: str, close: str = "16:00") -> ExchangeSession:
    zone = ZoneInfo("America/New_York")
    return ExchangeSession(
        calendar_id="fixture-US",
        session_date=date.fromisoformat(day),
        timezone=zone.key,
        open_at=datetime.fromisoformat(f"{day}T09:30").replace(tzinfo=zone),
        close_at=datetime.fromisoformat(f"{day}T{close}").replace(tzinfo=zone),
    )


@dataclass
class FixtureCalendar:
    # Explicit fixture sessions, not a weekday-based production calendar.
    values: tuple[ExchangeSession, ...] = (
        session("2025-03-07"),
        session("2025-03-10"),
        session("2025-07-03", "13:00"),
        session("2025-07-07"),
        session("2025-10-31"),
        session("2025-11-03"),
    )
    calendar_id: str = "fixture-US"
    version: str = "test-fixture-1"
    timezone: str = "America/New_York"
    supported_start: date = date(2025, 3, 7)
    supported_end: date = date(2025, 11, 3)

    def sessions_through(self, cutoff: datetime) -> tuple[ExchangeSession, ...]:
        return tuple(s for s in self.values if s.open_at <= cutoff)


def capability(**overrides: Any) -> ProviderCapability:
    # Synthetic verified timing ONLY for tests. Not Twelve Data defaults.
    return ProviderCapability.model_validate(
        {
            "interval": "1day",
            "timestamp_semantics": "candle_open",
            "regular_session_buckets": "session_open",
            "publication_delay_seconds": 60,
            "finalization_delay_seconds": 120,
            "evidence": "synthetic fixture",
            **overrides,
        }
    )


def resolve(at: str, cap: ProviderCapability | None = None, **kwargs: Any) -> Any:
    return resolve_eligibility(
        FrozenCutoff(cutoff_at=instant(at)), FixtureCalendar(), cap or capability(), **kwargs
    )


@pytest.mark.parametrize(
    "at,day,close",
    [
        ("2025-03-08T10:00:00Z", "2025-03-07", "2025-03-07T21:00:00Z"),
        ("2025-03-10T13:29:59Z", "2025-03-07", "2025-03-07T21:00:00Z"),
        ("2025-03-10T20:01:00Z", "2025-03-10", "2025-03-10T20:00:00Z"),
        ("2025-07-03T17:01:00Z", "2025-07-03", "2025-07-03T17:00:00Z"),
        ("2025-07-04T18:00:00Z", "2025-07-03", "2025-07-03T17:00:00Z"),
        ("2025-07-07T13:29:59Z", "2025-07-03", "2025-07-03T17:00:00Z"),
        ("2025-11-01T10:00:00Z", "2025-10-31", "2025-10-31T20:00:00Z"),
        ("2025-11-03T21:01:00Z", "2025-11-03", "2025-11-03T21:00:00Z"),
    ],
)
def test_daily_expected_sessions_dst_holiday_early_close(at: str, day: str, close: str) -> None:
    r = resolve(at, observation=None)
    assert r.reason == "missing_bar"
    assert r.expected.session_date == date.fromisoformat(day)
    assert r.expected.close_at == instant(close)
    assert r.calendar_version == "test-fixture-1"


def test_daily_publication_boundary_and_finalization_not_retrieval_alone() -> None:
    at = "2025-03-10T20:01:00Z"
    assert resolve("2025-03-10T20:00:59Z", observation=None).expected.session_date == date(
        2025, 3, 7
    )
    expected = resolve(at, observation=None).expected
    provisional = CachedCandle(identity=expected, retrieved_at=instant(at))
    assert resolve(at, observation=provisional).reason == "provisional_bar"
    refreshed = CachedCandle(
        identity=expected, retrieved_at=instant("2025-03-10T20:03:00Z"), finalized=True
    )
    # Even a later fetch cannot make it finalized at an earlier cutoff.
    assert resolve(at, observation=refreshed).reason == "provisional_bar"
    r = resolve("2025-03-10T20:02:00Z", observation=refreshed)
    assert r.status == "current" and r.is_complete
    assert r.retrieved_at > r.cutoff_at
    early_fetch = refreshed.model_copy(update={"retrieved_at": instant(at)})
    assert resolve("2025-03-10T20:03:00Z", observation=early_fetch).reason == "provisional_bar"


def test_latest_identity_not_last_cached_or_recently_retrieved() -> None:
    old = resolve("2025-03-08T10:00:00Z", observation=None).expected
    observation = CachedCandle(
        identity=old, retrieved_at=instant("2025-03-10T20:03:00Z"), finalized=True
    )
    assert resolve("2025-03-10T20:03:00Z", observation=observation).reason == "stale_bar"


@pytest.mark.parametrize(
    "at,opened,closed",
    [
        ("2025-03-10T13:36:00Z", "2025-03-10T13:30:00Z", "2025-03-10T13:35:00Z"),
        ("2025-03-10T13:40:59Z", "2025-03-10T13:30:00Z", "2025-03-10T13:35:00Z"),
        ("2025-03-10T13:41:00Z", "2025-03-10T13:35:00Z", "2025-03-10T13:40:00Z"),
    ],
)
def test_intraday_bucket_and_delay_boundary(at: str, opened: str, closed: str) -> None:
    r = resolve(at, capability(interval="5min"), observation=None)
    assert r.expected.open_at == instant(opened) and r.expected.close_at == instant(closed)


def test_short_final_bucket_uses_calendar_close() -> None:
    r = resolve("2025-07-03T17:03:00Z", capability(interval="1h"), observation=None)
    assert r.expected.open_at == instant("2025-07-03T16:30:00Z")
    assert r.expected.close_at == instant("2025-07-03T17:00:00Z")


def test_unknown_capabilities_and_calendar_fail_closed() -> None:
    cutoff = FrozenCutoff(cutoff_at=instant("2025-03-10T20:03:00Z"))
    unknown = ProviderCapability(interval="1day")
    assert (
        resolve_eligibility(cutoff, FixtureCalendar(), unknown, None).reason == "unknown_capability"
    )
    assert resolve_eligibility(cutoff, None, capability(), None).reason == "calendar_unavailable"
    assert resolve("2026-01-01T00:00:00Z", observation=None).reason == "calendar_out_of_range"
    assert (
        resolve(
            "2025-03-10T20:03:00Z", capability(finalization_delay_seconds=None), observation=None
        ).reason
        == "unknown_capability"
    )
    assert (
        resolve("2025-03-10T20:03:00Z", capability(interval="1week"), observation=None).reason
        == "unknown_capability"
    )


def test_unfinished_requires_verified_bounded_snapshot() -> None:
    at = "2025-03-10T13:33:00Z"
    assert resolve(at, observation=None, include_unfinished=True).reason == "unfinished_unavailable"
    cap = capability(
        interval="5min", unfinished_snapshots_verified=True, unfinished_max_age_seconds=30
    )
    expected = resolve(at, cap, observation=None, include_unfinished=True).expected
    observation = CachedCandle(identity=expected, retrieved_at=instant(at), snapshot_at=instant(at))
    r = resolve(at, cap, observation=observation, include_unfinished=True)
    assert r.status == "current" and not r.is_complete
    assert r.expected.open_at == instant("2025-03-10T13:30:00Z")
    for snapshot, reason in [
        (None, "invalid_snapshot"),
        ("2025-03-10T13:32:29Z", "stale_snapshot"),
        ("2025-03-10T13:33:01Z", "invalid_snapshot"),
    ]:
        changed = CachedCandle(
            identity=expected,
            retrieved_at=instant("2025-03-10T13:34:00Z"),
            snapshot_at=instant(snapshot) if snapshot else None,
        )
        assert resolve(at, cap, observation=changed, include_unfinished=True).reason == reason
    # Completed-only never silently consumes that forming candle.
    assert resolve(at, cap, observation=observation).reason == "stale_bar"


def test_unfinished_opt_in_after_hours_resolves_completed_bar() -> None:
    at = "2025-03-10T20:03:00Z"
    cap = capability(unfinished_snapshots_verified=True, unfinished_max_age_seconds=30)
    expected = resolve(at, cap, observation=None, include_unfinished=True).expected
    observation = CachedCandle(identity=expected, retrieved_at=instant(at), finalized=True)
    assert resolve(at, cap, observation=observation, include_unfinished=True).is_complete


def test_cutoff_is_frozen_once_and_naive_times_rejected() -> None:
    class CountingClock:
        calls = 0

        def now(self) -> datetime:
            self.calls += 1
            return instant("2025-03-10T20:03:00Z")

    clock = CountingClock()
    frozen = FrozenCutoff.freeze(clock)
    for _ in range(2):
        resolve_eligibility(frozen, FixtureCalendar(), capability(), None)
    assert clock.calls == 1
    with pytest.raises(ValidationError):
        FrozenCutoff(cutoff_at=datetime(2025, 3, 10))


def test_invalid_policy_and_observation() -> None:
    with pytest.raises(ValidationError):
        capability(finalization_delay_seconds=1)
    with pytest.raises(ValidationError):
        capability(unfinished_snapshots_verified=True)
    identity = resolve("2025-03-10T20:03:00Z", observation=None).expected
    with pytest.raises(ValidationError):
        CachedCandle(identity=identity, retrieved_at=instant("2025-03-10T10:00:00Z"))
    with pytest.raises(ValidationError):
        CachedCandle(
            identity=identity, retrieved_at=instant("2025-03-10T14:00:00Z"), finalized=True
        )
