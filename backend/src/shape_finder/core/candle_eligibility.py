"""Pure, fail-closed candle policy. No calendar, provider, or persistence I/O."""

from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from shape_finder.core.market_data import BarInterval


class PolicyModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FrozenCutoff(PolicyModel):
    cutoff_at: AwareDatetime

    @classmethod
    def freeze(cls, clock: Clock) -> "FrozenCutoff":
        return cls(cutoff_at=clock.now())


class ExchangeSession(PolicyModel):
    calendar_id: str = Field(min_length=1)
    session_date: date
    timezone: str
    open_at: AwareDatetime
    close_at: AwareDatetime

    @model_validator(mode="after")
    def valid_session(self) -> "ExchangeSession":
        zone = ZoneInfo(self.timezone)
        if self.close_at <= self.open_at:
            raise ValueError("Session close must follow its open.")
        if self.open_at.astimezone(zone).date() != self.session_date:
            raise ValueError("Session date must identify the local session open.")
        return self


class ExchangeCalendar(Protocol):
    """Adapter must supply an authoritative, complete schedule within these dates.

    This stage intentionally provides no production calendar implementation.
    Session boundaries include holiday closures, DST and special/early closes.
    """

    calendar_id: str
    version: str
    timezone: str
    supported_start: date
    supported_end: date

    def sessions_through(self, cutoff: datetime) -> Sequence[ExchangeSession]: ...


class ProviderCapability(PolicyModel):
    interval: BarInterval
    timestamp_semantics: Literal["unknown", "candle_open"] = "unknown"
    regular_session_buckets: Literal["unknown", "session_open"] = "unknown"
    publication_delay_seconds: int | None = Field(default=None, ge=0, strict=True)
    finalization_delay_seconds: int | None = Field(default=None, ge=0, strict=True)
    next_trading_day_publication: bool = False
    unfinished_snapshots_verified: bool = False
    unfinished_max_age_seconds: int | None = Field(default=None, ge=0, strict=True)
    evidence: str | None = None

    @model_validator(mode="after")
    def coherent_policy(self) -> "ProviderCapability":
        if self.next_trading_day_publication and (
            self.interval is not BarInterval.ONE_DAY
            or self.publication_delay_seconds is not None
            or self.finalization_delay_seconds is not None
            or self.unfinished_snapshots_verified
        ):
            raise ValueError("Next-trading-day publication requires completed daily candles.")
        if (
            self.publication_delay_seconds is not None
            and self.finalization_delay_seconds is not None
            and self.finalization_delay_seconds < self.publication_delay_seconds
        ):
            raise ValueError("Finalization cannot precede publication.")
        if self.unfinished_snapshots_verified and self.unfinished_max_age_seconds is None:
            raise ValueError("Verified snapshots require an explicit freshness bound.")
        return self


class CapabilitySource(Protocol):
    def for_interval(self, interval: BarInterval) -> ProviderCapability: ...


class UnverifiedProviderCapabilities:
    """Safe adapter default until interval-specific timing is verified."""

    def for_interval(self, interval: BarInterval) -> ProviderCapability:
        return ProviderCapability(interval=interval)


class CandleIdentity(PolicyModel):
    interval: BarInterval
    calendar_id: str
    session_date: date
    timezone: str
    open_at: AwareDatetime
    close_at: AwareDatetime

    @model_validator(mode="after")
    def ordered(self) -> "CandleIdentity":
        if self.open_at >= self.close_at:
            raise ValueError("Candle close must follow open.")
        if self.open_at.astimezone(ZoneInfo(self.timezone)).date() != self.session_date:
            raise ValueError("Candle session date must match its local open.")
        return self


class CachedCandle(PolicyModel):
    identity: CandleIdentity
    retrieved_at: AwareDatetime
    finalized: bool = False
    snapshot_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def coherent_observation(self) -> "CachedCandle":
        if self.retrieved_at < self.identity.open_at:
            raise ValueError("Retrieval precedes candle open.")
        if self.finalized and self.retrieved_at < self.identity.close_at:
            raise ValueError("A finalized observation cannot precede candle close.")
        if self.snapshot_at is not None and not (
            self.identity.open_at <= self.snapshot_at <= self.retrieved_at
        ):
            raise ValueError("Snapshot time must lie between candle open and retrieval.")
        return self


class ExclusionReason(StrEnum):
    UNKNOWN_CAPABILITY = "unknown_capability"
    CALENDAR_UNAVAILABLE = "calendar_unavailable"
    CALENDAR_OUT_OF_RANGE = "calendar_out_of_range"
    NO_EXPECTED_CANDLE = "no_expected_candle"
    UNFINISHED_UNAVAILABLE = "unfinished_unavailable"
    MISSING_BAR = "missing_bar"
    STALE_BAR = "stale_bar"
    PROVISIONAL_BAR = "provisional_bar"
    INVALID_SNAPSHOT = "invalid_snapshot"
    STALE_SNAPSHOT = "stale_snapshot"


class Freshness(PolicyModel):
    cutoff_at: AwareDatetime
    status: Literal["current", "excluded"]
    reason: ExclusionReason | None = None
    expected: CandleIdentity | None = None
    retrieved_at: AwareDatetime | None = None
    snapshot_at: AwareDatetime | None = None
    is_complete: bool | None = None
    publishable_at: AwareDatetime | None = None
    finalizable_at: AwareDatetime | None = None
    calendar_version: str | None = None

    @model_validator(mode="after")
    def coherent_status(self) -> "Freshness":
        if self.status == "current":
            if self.reason is not None or any(
                value is None for value in (self.expected, self.retrieved_at, self.is_complete)
            ):
                raise ValueError("Current freshness requires identity, retrieval and completeness.")
            assert self.expected is not None and self.retrieved_at is not None
            if not self.calendar_version:
                raise ValueError("Current freshness requires calendar version evidence.")
            if self.is_complete:
                if (
                    self.publishable_at is None
                    or self.finalizable_at is None
                    or not self.expected.close_at
                    <= self.publishable_at
                    <= self.finalizable_at
                    <= self.cutoff_at
                    or self.retrieved_at < self.finalizable_at
                ):
                    raise ValueError("Completed freshness requires verified finalization timing.")
            elif (
                self.snapshot_at is None
                or not self.expected.open_at
                <= self.snapshot_at
                <= self.cutoff_at
                < self.expected.close_at
                or self.snapshot_at > self.retrieved_at
            ):
                raise ValueError("Unfinished freshness requires an eligible snapshot time.")
        elif self.reason is None:
            raise ValueError("Excluded freshness requires a reason.")
        return self


_STEPS = {
    BarInterval.ONE_MINUTE: timedelta(minutes=1),
    BarInterval.FIVE_MINUTES: timedelta(minutes=5),
    BarInterval.FIFTEEN_MINUTES: timedelta(minutes=15),
    BarInterval.THIRTY_MINUTES: timedelta(minutes=30),
    BarInterval.ONE_HOUR: timedelta(hours=1),
}


def next_session_publications(
    sessions: Sequence[ExchangeSession], timezone: str
) -> dict[date, datetime]:
    zone = ZoneInfo(timezone)
    ordered = sorted(sessions, key=lambda session: session.session_date)
    return {
        current.session_date: datetime.combine(following.session_date, time.min, zone).astimezone(
            UTC
        )
        for current, following in zip(ordered, ordered[1:], strict=False)
    }


def resolve_eligibility(
    cutoff: FrozenCutoff,
    calendar: ExchangeCalendar | None,
    capability: ProviderCapability,
    observation: CachedCandle | None,
    *,
    include_unfinished: bool = False,
) -> Freshness:
    """Resolve one expected latest candle; never substitute the last cached bar.

    Later retrieval is permitted for completed observations, but is not a
    historical point-in-time data guarantee. Unfinished observations must have
    an independently verified snapshot timestamp no later than the frozen cutoff.
    """
    now = cutoff.cutoff_at
    expected: CandleIdentity | None = None
    complete: bool | None = None
    publishable: datetime | None = None
    finalizable: datetime | None = None

    def result(reason: ExclusionReason | None = None) -> Freshness:
        return Freshness(
            cutoff_at=now,
            status="excluded" if reason else "current",
            reason=reason,
            expected=expected,
            retrieved_at=observation.retrieved_at if observation else None,
            snapshot_at=observation.snapshot_at if observation else None,
            is_complete=complete,
            publishable_at=publishable,
            finalizable_at=finalizable,
            calendar_version=calendar.version if calendar else None,
        )

    if calendar is None:
        return result(ExclusionReason.CALENDAR_UNAVAILABLE)
    local_date = now.astimezone(ZoneInfo(calendar.timezone)).date()
    if not calendar.supported_start <= local_date <= calendar.supported_end:
        return result(ExclusionReason.CALENDAR_OUT_OF_RANGE)
    if (
        capability.timestamp_semantics != "candle_open"
        or capability.regular_session_buckets != "session_open"
        or (
            not capability.next_trading_day_publication
            and (
                capability.publication_delay_seconds is None
                or capability.finalization_delay_seconds is None
            )
        )
        or capability.interval not in (*_STEPS, BarInterval.ONE_DAY)
    ):
        return result(ExclusionReason.UNKNOWN_CAPABILITY)
    if include_unfinished and not capability.unfinished_snapshots_verified:
        return result(ExclusionReason.UNFINISHED_UNAVAILABLE)

    publication = timedelta(seconds=capability.publication_delay_seconds or 0)
    finalization = timedelta(seconds=capability.finalization_delay_seconds or 0)
    sessions = sorted(calendar.sessions_through(now), key=lambda s: s.open_at)
    schedule = (
        next_session_publications(sessions, calendar.timezone)
        if capability.next_trading_day_publication
        else None
    )
    for session in sessions:
        if session.calendar_id != calendar.calendar_id or session.timezone != calendar.timezone:
            return result(ExclusionReason.CALENDAR_UNAVAILABLE)
        opened = session.open_at.astimezone(UTC)
        session_close = session.close_at.astimezone(UTC)
        step = (
            session_close - opened
            if capability.interval is BarInterval.ONE_DAY
            else _STEPS[capability.interval]
        )
        while opened < session_close:
            closed = min(opened + step, session_close)
            if schedule is None:
                published_at, ready_at = closed + publication, closed + finalization
            else:
                published = schedule.get(session.session_date)
                if published is None:
                    break
                published_at = ready_at = published
            finished = closed <= now and published_at <= now
            forming = include_unfinished and opened <= now < closed
            if finished or forming:
                expected = CandleIdentity(
                    interval=capability.interval,
                    calendar_id=session.calendar_id,
                    session_date=session.session_date,
                    timezone=session.timezone,
                    open_at=opened,
                    close_at=closed,
                )
                complete = not forming
                publishable = published_at
                finalizable = ready_at
            opened = closed
    if expected is None:
        return result(ExclusionReason.NO_EXPECTED_CANDLE)
    if observation is None:
        return result(ExclusionReason.MISSING_BAR)
    if observation.identity != expected:
        return result(ExclusionReason.STALE_BAR)
    if complete:
        assert finalizable is not None
        if not observation.finalized or now < finalizable or observation.retrieved_at < finalizable:
            return result(ExclusionReason.PROVISIONAL_BAR)
    else:
        snapshot = observation.snapshot_at
        if (
            observation.finalized
            or snapshot is None
            or not expected.open_at <= snapshot <= now < expected.close_at
        ):
            return result(ExclusionReason.INVALID_SNAPSHOT)
        assert capability.unfinished_max_age_seconds is not None
        if now - snapshot > timedelta(seconds=capability.unfinished_max_age_seconds):
            return result(ExclusionReason.STALE_SNAPSHOT)
    return result()
