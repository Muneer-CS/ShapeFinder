"""Documented semantics plus explicit operator policy, without invented timing defaults."""

from shape_finder.core.candle_eligibility import ProviderCapability
from shape_finder.core.current_data import CapabilityReport, TimingPolicy
from shape_finder.core.market_data import BarInterval

SOURCE = "https://twelvedata.com/docs/market-data/time-series"


def capability_report(
    interval: BarInterval, policy: TimingPolicy | None = None
) -> CapabilityReport:
    base = ProviderCapability(interval=interval, timestamp_semantics="candle_open", evidence=SOURCE)
    if interval is BarInterval.ONE_WEEK:
        return CapabilityReport(
            status="unavailable", reason="interval_unavailable", capability=base
        )
    if policy is None:
        return CapabilityReport(
            status="unavailable", reason="publication_finalization_unknown", capability=base
        )
    if policy.interval != interval.value:
        return CapabilityReport(
            status="unavailable", reason="policy_interval_mismatch", capability=base
        )
    if interval is not BarInterval.ONE_DAY and not policy.session_open_buckets_confirmed:
        return CapabilityReport(
            status="unavailable", reason="bucket_alignment_unknown", capability=base, policy=policy
        )
    cap = ProviderCapability(
        interval=interval,
        timestamp_semantics="candle_open",
        regular_session_buckets="session_open",
        publication_delay_seconds=policy.publication_delay_seconds,
        finalization_delay_seconds=policy.finalization_delay_seconds,
        next_trading_day_publication=policy.next_trading_day_publication,
        unfinished_snapshots_verified=False,
        evidence=policy.provenance,
    )
    return CapabilityReport(
        status="usable" if policy.basis == "verified" else "configured_but_unverified",
        reason="explicit_timing_policy",
        capability=cap,
        policy=policy,
    )
