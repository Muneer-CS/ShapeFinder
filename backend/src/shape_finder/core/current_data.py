"""Internal production-data contracts, not public scan orchestration."""

import hashlib
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from shape_finder.core.candle_eligibility import CandleIdentity, PolicyModel, ProviderCapability
from shape_finder.core.market_data import PriceBar


class DataUnavailable(ValueError):
    """Stable reason string suitable for internal capability/coverage reports."""


class TimingPolicy(PolicyModel):
    interval: Literal["1day", "1min", "5min", "15min", "30min", "1h"]
    publication_delay_seconds: int | None = Field(default=None, ge=0, le=604800, strict=True)
    finalization_delay_seconds: int | None = Field(default=None, ge=0, le=604800, strict=True)
    next_trading_day_publication: bool = False
    basis: Literal["configured", "verified"] = "configured"
    provenance: str = Field(min_length=10, max_length=1000)
    reviewed_at: AwareDatetime
    # Intraday OHLC aggregation needs independent evidence, not a timezone guess.
    session_open_buckets_confirmed: bool = False
    bucket_provenance: str | None = Field(default=None, min_length=10, max_length=1000)

    @model_validator(mode="after")
    def coherent(self) -> "TimingPolicy":
        if self.next_trading_day_publication:
            if (
                self.interval != "1day"
                or self.publication_delay_seconds is not None
                or self.finalization_delay_seconds is not None
                or self.basis != "configured"
            ):
                raise ValueError("Next-trading-day publication is a configured daily policy.")
        elif self.publication_delay_seconds is None or self.finalization_delay_seconds is None:
            raise ValueError("Fixed timing requires both delays.")
        elif self.finalization_delay_seconds < self.publication_delay_seconds:
            raise ValueError("Finalization precedes publication.")
        if self.session_open_buckets_confirmed and self.bucket_provenance is None:
            raise ValueError("Bucket confirmation requires provenance.")
        return self


class CapabilityReport(PolicyModel):
    status: Literal["usable", "configured_but_unverified", "unavailable"]
    reason: str
    capability: ProviderCapability
    policy: TimingPolicy | None = None

    def policy_key(self, calendar_version: str) -> str:
        # Existing fixed-delay observations retain their cache identity.
        fixed_fields = {
            "capability": {"next_trading_day_publication"},
            "policy": {"next_trading_day_publication"},
        }
        policy_json = self.model_dump_json(
            exclude=None if self.capability.next_trading_day_publication else fixed_fields
        )
        return hashlib.sha256(
            (policy_json + "|" + calendar_version + "|canonical-v1").encode()
        ).hexdigest()


class CanonicalBar(PolicyModel):
    identity: CandleIdentity
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    retrieved_at: AwareDatetime
    policy_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    calendar_version: str

    @model_validator(mode="after")
    def valid_observation(self) -> "CanonicalBar":
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close):
            raise ValueError("Inconsistent OHLC.")
        if self.retrieved_at < self.identity.close_at:
            raise ValueError("This cache accepts completed observations only.")
        return self

    def price_bar(self) -> PriceBar:
        return PriceBar(
            self.identity.open_at, self.open, self.high, self.low, self.close, self.volume
        )


class DispatchLimits(PolicyModel):
    daily_attempts: int = Field(default=6, ge=1, le=100, strict=True)
    intraday_attempts: int = Field(default=3, ge=1, le=100, strict=True)
    daily_symbols: int = Field(default=5, ge=1, le=50, strict=True)
    intraday_symbols: int = Field(default=2, ge=1, le=50, strict=True)
    timeout_seconds: float = Field(default=20, gt=0, le=120)
    cooldown_seconds: float = Field(default=60, gt=0, le=86400)


class AccountPolicy(PolicyModel):
    # None means unknown, NOT unlimited verified allowance.
    attempts_per_60_seconds: int | None = Field(default=None, ge=1, strict=True)
    credits_per_86400_seconds: int | None = Field(default=None, ge=1, strict=True)
    provenance: str = Field(min_length=10, max_length=1000)
