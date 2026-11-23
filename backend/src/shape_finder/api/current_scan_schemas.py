"""Current-pattern public API schemas reuse validated domain contracts."""

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, Field

from shape_finder.core.candle_eligibility import PolicyModel
from shape_finder.core.pattern_scan import CurrentScanRequest, CurrentScanResult, Revisions


class IntervalCapability(PolicyModel):
    interval: str
    available: bool
    status: Literal["usable", "configured_but_unverified", "unavailable"]
    reason: str
    timing_basis: Literal["unknown", "operator_configured", "operator_attested"]
    provider_timing_guarantee_verified: bool = False
    unfinished_available: bool = False
    unfinished_reason: str = "snapshot_timing_unverified"


class PatternCapabilities(PolicyModel):
    intervals: tuple[IntervalCapability, ...]
    calendar_implementation: str = "exchange_calendars"
    calendar_version: str
    calendar_start: date = date(2000, 1, 1)
    calendar_end: date = date(2030, 12, 31)
    supported_listing_exchanges: tuple[str, ...] = ("NYSE", "NASDAQ", "XNYS", "XNAS")
    hydration_configured: bool
    accept_configured_policies: bool


class ProviderUsage(PolicyModel):
    http_attempts: int = Field(default=0, ge=0)
    documented_credit_cost: int = Field(default=0, ge=0)
    unknown_cost_attempts: int = Field(default=0, ge=0)
    actual_credits_consumed: None = None
    scope: Literal["current_scan_only"] = "current_scan_only"


class UniverseSnapshot(PolicyModel):
    kind: str
    frozen_at: AwareDatetime
    catalog_refreshed_at: AwareDatetime | None = None
    catalog_stale: bool
    selected_candidates: int
    selection_policy: str = "sha256_ring_process_local_v1"


class CurrentPatternScanRequest(CurrentScanRequest):
    """Request schema; snake_case names and explicit units are shared end to end."""


class CurrentPatternScanResponse(CurrentScanResult):
    """Response schema; computed completion and match type are output-only fields."""

    outcome: Literal["evaluated", "partial"] = "evaluated"
    provider_usage: ProviderUsage = Field(default_factory=ProviderUsage)
    universe_snapshot: UniverseSnapshot | None = None
    capability: IntervalCapability | None = None
    reason_counts: dict[str, int] = Field(default_factory=dict)
    evaluated_session_dates: dict[str, date] = Field(default_factory=dict)


class PatternScanError(PolicyModel):
    code: str = "CURRENT_SCAN_UNAVAILABLE"
    message: str = "The current scan could not evaluate any stocks."
    reason: str
    request_id: str
    details: CurrentPatternScanResponse | None = None
    revisions: Revisions | None = None
    pattern_content_hash: str | None = None


class PatternScanErrorResponse(PolicyModel):
    error: PatternScanError
