"""Offline computation only. Inputs carry independently established candle eligibility."""

import math
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal, DecimalException, localcontext
from fractions import Fraction
from typing import Literal
from zoneinfo import ZoneInfo

from shape_finder.application.current_shape import (
    PreparedShape,
    ShapeScore,
    log_close_path,
    prepare_candidate,
    prepare_prefix,
    score_pairs,
)
from shape_finder.core.candle_eligibility import CandleIdentity, Freshness, FrozenCutoff
from shape_finder.core.market_data import TimeSeries, validate_time_series
from shape_finder.core.pattern_scan import (
    CanonicalPattern,
    ComparisonObservation,
    Coverage,
    CurrentPatternMatch,
    CurrentScanSettings,
    ExecutionStatus,
    MatchComparison,
    ShapeComponents,
    completion_fraction,
)

StopReason = Literal["deadline", "cancelled", "computation_budget"]
Category = Literal["stale", "skipped", "failed", "not_evaluated"]


@dataclass(frozen=True)
class EligibleHistory:
    symbol: str
    series: TimeSeries | None
    freshness: Freshness
    # Complete calendar-derived sequence of required observations in the supplied
    # history horizon, ending at freshness.expected. Not inferred from cache bars.
    required_candles: tuple[CandleIdentity, ...]


@dataclass(frozen=True)
class ScanControl:
    deadline_at: float | None = None  # Absolute injected monotonic time, not market cutoff.
    monotonic: Callable[[], float] = time.monotonic
    cancelled: Callable[[], bool] = lambda: False

    def stop_reason(self) -> StopReason | None:
        if self.cancelled():
            return "cancelled"
        if self.deadline_at is not None and self.monotonic() >= self.deadline_at:
            return "deadline"
        return None


@dataclass(frozen=True)
class ExcludedHistory:
    symbol: str
    category: Category
    reason: str


@dataclass(frozen=True)
class CurrentScanEvaluation:
    matches: tuple[CurrentPatternMatch, ...]
    coverage: Coverage
    execution: ExecutionStatus
    exclusions: tuple[ExcludedHistory, ...]
    scoring_version: Literal["current_shape_v1"] = "current_shape_v1"


@dataclass(frozen=True)
class Hypothesis:
    complete: int
    observed: int
    completion: Fraction


def hypotheses(settings: CurrentScanSettings, available: int) -> Iterator[Hypothesis]:
    if settings.match_type == "full":
        for complete in range(
            settings.duration.minimum_candles, min(settings.duration.maximum_candles, available) + 1
        ):
            yield Hypothesis(complete, complete, Fraction(1))
        return
    minimum = Fraction(str(settings.minimum_completion))
    for complete in range(settings.duration.minimum_candles, settings.duration.maximum_candles + 1):
        # Integer ceiling of minimum * (D-1), then convert intervals to observations.
        required = minimum * (complete - 1)
        first = max(8, -(-required.numerator // required.denominator) + 1)
        for observed in range(first, min(complete, available) + 1):
            if settings.match_type == "forming" and observed == complete:
                continue
            yield Hypothesis(complete, observed, completion_fraction(observed, complete))


def _validate_history(
    history: EligibleHistory, settings: CurrentScanSettings, cutoff: FrozenCutoff
) -> tuple[Category, str] | None:
    fresh = history.freshness
    if fresh.status != "current":
        reason = fresh.reason.value if fresh.reason else "unknown_freshness"
        stale = reason in {
            "stale_bar",
            "missing_bar",
            "provisional_bar",
            "stale_snapshot",
            "invalid_snapshot",
        }
        return ("stale" if stale else "skipped"), reason
    expected = fresh.expected
    if fresh.cutoff_at != cutoff.cutoff_at or expected is None:
        return "stale", "cutoff_mismatch"
    if expected.interval.value != settings.interval:
        return "skipped", "interval_mismatch"
    if not settings.include_unfinished and not fresh.is_complete:
        return "skipped", "unfinished_disabled"
    series = history.series
    if series is None:
        return "skipped", "history_unavailable"
    if len(series.bars) > 10_000 or len(history.required_candles) > 10_000:
        return "failed", "history_input_limit"
    try:
        validate_time_series(series)
    except (ValueError, ArithmeticError, AttributeError):
        return "failed", "malformed_history"
    if series.symbol != history.symbol or series.interval.value != settings.interval:
        return "failed", "history_identity_mismatch"
    if any(a.timestamp >= b.timestamp for a, b in zip(series.bars, series.bars[1:], strict=False)):
        return "failed", "unordered_history"
    if not series.bars or series.bars[-1].timestamp != expected.open_at:
        return "stale", "mismatched_end"
    required = history.required_candles
    if not required or required[-1] != expected:
        return "skipped", "required_coverage_unavailable"
    if any(
        item.interval != expected.interval
        or item.calendar_id != expected.calendar_id
        or item.timezone != expected.timezone
        for item in required
    ) or any(a.close_at > b.open_at for a, b in zip(required, required[1:], strict=False)):
        return "failed", "malformed_required_coverage"
    # Validate exact observed identities against the declared schedule. Calendar
    # closures are legitimate gaps; missing required observations are not.
    required_tail = required[-settings.duration.maximum_candles :]
    observed = tuple(
        bar.timestamp for bar in series.bars if bar.timestamp >= required_tail[0].open_at
    )
    if observed != tuple(item.open_at for item in required_tail):
        return "skipped", "missing_required_observations"
    if next(hypotheses(settings, len(required_tail)), None) is None:
        return "skipped", "insufficient_history"
    return None


def _alignment_key(
    hypothesis: Hypothesis, similarity: float, exact_similarity: bool
) -> tuple[float, Fraction, int, int]:
    return (
        -(similarity if exact_similarity else round(similarity, 6)),
        -hypothesis.completion,
        -hypothesis.observed,
        hypothesis.complete,
    )


def match_rank_key(match: CurrentPatternMatch) -> tuple[int, float, str]:
    session = match.last_candle_session
    if session is None:
        return (0, -round(match.similarity_score, 6), match.symbol)
    return (-session.toordinal(), -match.similarity_score, match.symbol)


class CurrentPatternScanner:
    def __init__(self, *, max_hypotheses: int = 1_000_000, batch_size: int = 256) -> None:
        if type(max_hypotheses) is not int or not 0 <= max_hypotheses <= 1_000_000:
            raise ValueError("Server hypothesis limit must lie in [0,1000000].")
        if type(batch_size) is not int or not 1 <= batch_size <= 256:
            raise ValueError("Batch size must lie in [1,256].")
        self.max_hypotheses = max_hypotheses
        self.batch_size = batch_size

    def scan(
        self,
        pattern: CanonicalPattern,
        settings: CurrentScanSettings,
        cutoff: FrozenCutoff,
        histories: Sequence[EligibleHistory],
        *,
        control: ScanControl | None = None,
        recent_daily: bool = False,
    ) -> CurrentScanEvaluation:
        if len(histories) > 50_000 or len({h.symbol for h in histories}) != len(histories):
            raise ValueError("Input universe must contain unique symbols, at most 50000.")
        active_control = control or ScanControl()
        prefixes: dict[Fraction, PreparedShape | None] = {}
        matches: list[CurrentPatternMatch] = []
        exclusions: list[ExcludedHistory] = []
        minimum_movement = Decimal(str(settings.minimum_movement_percent))
        scanned = eligible = incomplete = work = 0
        stop: StopReason | None = None
        for history in sorted(histories, key=lambda h: h.symbol):
            stop = stop or active_control.stop_reason()
            if stop is not None:
                exclusions.append(ExcludedHistory(history.symbol, "not_evaluated", stop))
                continue
            problem = _validate_history(history, settings, cutoff)
            if problem is not None:
                exclusions.append(ExcludedHistory(history.symbol, *problem))
                continue
            eligible += 1
            assert history.series is not None
            available = min(len(history.required_candles), settings.duration.maximum_candles)
            bars = history.series.bars[-available:]
            try:
                candidates: dict[int, tuple[PreparedShape | None, float]] = {}
                first = settings.duration.minimum_candles if settings.match_type == "full" else 8
                last = available
                if recent_daily:
                    first = next(hypotheses(settings, available)).observed
                    if settings.match_type == "forming":
                        last = min(last, settings.duration.maximum_candles - 1)
                for n in range(first, last + 1):
                    closes = [bar.close for bar in bars[-n:]]
                    with localcontext() as context:
                        context.prec = 50
                        observed_movement = 100 * (max(closes) / min(closes) - 1)
                    numeric_movement = float(observed_movement)
                    if not math.isfinite(numeric_movement):
                        raise ValueError("Movement cannot be represented.")
                    if observed_movement >= minimum_movement:
                        candidates[n] = (
                            prepare_candidate(log_close_path(closes)),
                            numeric_movement,
                        )
            except (ValueError, DecimalException, OverflowError):
                eligible -= 1
                exclusions.append(
                    ExcludedHistory(history.symbol, "failed", "unrepresentable_history")
                )
                continue
            best: tuple[Hypothesis, ShapeScore, float] | None = None
            best_key: tuple[float, Fraction, int, int] | None = None
            started = False
            iterator = hypotheses(settings, available)
            exhausted = False
            while not exhausted:
                stop = active_control.stop_reason()
                if stop:
                    break
                batch: list[tuple[Hypothesis, PreparedShape, PreparedShape, float]] = []
                # Bound preparation too: a batch examines at most batch_size hypotheses.
                for _ in range(self.batch_size):
                    h = next(iterator, None)
                    if h is None:
                        exhausted = True
                        break
                    started = True
                    candidate, movement = candidates.get(h.observed, (None, 0.0))
                    if candidate is None:
                        continue
                    if h.completion not in prefixes:
                        prefixes[h.completion] = prepare_prefix(pattern, h.completion)
                    prefix = prefixes[h.completion]
                    if prefix is not None:
                        batch.append((h, prefix, candidate, movement))
                remaining = self.max_hypotheses - work
                limited = len(batch) > remaining
                batch = batch[:remaining]
                scores = score_pairs([(left, right) for _, left, right, _ in batch])
                work += len(scores)
                for (h, _, _, movement), score in zip(batch, scores, strict=True):
                    if score.overall < settings.minimum_similarity:
                        continue
                    key = _alignment_key(h, score.overall, recent_daily)
                    if best_key is None or key < best_key:
                        best = h, score, movement
                        best_key = key
                stop = active_control.stop_reason()
                if limited:
                    stop = stop or "computation_budget"
                if stop:
                    break
            if stop:
                incomplete += int(started)
                exclusions.append(ExcludedHistory(history.symbol, "not_evaluated", stop))
                continue  # Discard any provisional best from this stock.
            scanned += 1
            if best is not None:
                h, score, movement = best
                start_at = bars[-h.observed].timestamp
                expected = history.required_candles[-1]
                matches.append(
                    CurrentPatternMatch(
                        symbol=history.symbol,
                        similarity_score=score.overall,
                        components=ShapeComponents(
                            shape=score.shape, direction=score.direction, error=score.error
                        ),
                        complete_duration_candles=h.complete,
                        observed_candles=h.observed,
                        movement_percent=movement,
                        start_at=start_at,
                        freshness=history.freshness,
                        matched_start_session=(
                            start_at.astimezone(ZoneInfo(expected.timezone)).date()
                            if recent_daily
                            else None
                        ),
                        last_candle_session=expected.session_date if recent_daily else None,
                    )
                )
        matches.sort(key=match_rank_key)
        # Snapshot only the selected, fully scored suffixes. No provider or cache
        # read occurs here, and the canonical request remains the reference.
        by_symbol = {history.symbol: history for history in histories}
        selected_items: list[CurrentPatternMatch] = []
        for match in matches[: settings.top_n]:
            series = by_symbol[match.symbol].series
            assert series is not None
            observed_bars = series.bars[-match.observed_candles :]
            # Preserve the ranked result even for an unusually large decimal
            # encoding. Normal responses are bounded by top_n * 240 samples.
            observations: list[ComparisonObservation] = []
            for bar in observed_bars:
                values = {"close": str(bar.close)}
                if recent_daily:
                    values.update(
                        open=str(bar.open),
                        high=str(bar.high),
                        low=str(bar.low),
                        volume=str(bar.volume),
                    )
                if any(len(text) > 128 for text in values.values()):
                    break
                observations.append(ComparisonObservation(timestamp=bar.timestamp, **values))
            if len(observations) != len(observed_bars):
                selected_items.append(match)
                continue
            comparison = MatchComparison(
                observations=tuple(observations),
                log_relative_closes=log_close_path([bar.close for bar in observed_bars]),
            )
            selected_items.append(
                CurrentPatternMatch(
                    **match.model_dump(
                        exclude={"comparison", "estimated_completion", "match_type"}
                    ),
                    comparison=comparison,
                )
            )
        selected = tuple(selected_items)
        counts = {
            category: sum(e.category == category for e in exclusions)
            for category in ("stale", "skipped", "failed", "not_evaluated")
        }
        coverage = Coverage(
            universe_total=len(histories),
            scanned=scanned,
            eligible=eligible,
            stale=counts["stale"],
            skipped=counts["skipped"],
            failed=counts["failed"],
            not_evaluated=counts["not_evaluated"],
            evaluation_incomplete=incomplete,
            matches_returned=len(selected),
        )
        return CurrentScanEvaluation(
            selected,
            coverage,
            ExecutionStatus(
                partial=scanned < len(histories),
                stop_reasons=(stop,) if stop else (),
                provider_requests=0,
                hypotheses_evaluated=work,
            ),
            tuple(exclusions),
        )
