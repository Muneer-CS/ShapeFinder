"""Validated current-scan contracts, deliberately independent of scan execution."""

import hashlib
import json
import re
import struct
from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import (
    AwareDatetime,
    Field,
    ValidationInfo,
    computed_field,
    field_validator,
    model_validator,
)

from shape_finder.core.candle_eligibility import Freshness, FrozenCutoff, PolicyModel
from shape_finder.core.market_data import BarInterval
from shape_finder.core.universe import UniverseKind


class PatternPoint(PolicyModel):
    x: float = Field(ge=0, le=1, strict=True)
    y: float = Field(ge=-1000, le=1000, strict=True)


class PatternProvenance(PolicyModel):
    kind: Literal["drawing", "stock_section", "text", "screenshot_trace"]
    source_symbol: str | None = Field(default=None, min_length=1, max_length=32)
    source_interval: BarInterval | None = None
    source_start_at: AwareDatetime | None = None
    source_end_at: AwareDatetime | None = None
    grammar_version: Literal[1] | None = None

    @model_validator(mode="after")
    def coherent_source(self) -> "PatternProvenance":
        if self.kind == "stock_section" and (
            self.source_symbol is None
            or self.source_interval is None
            or self.source_start_at is None
            or self.source_end_at is None
        ):
            raise ValueError("Stock sections require symbol, interval and source range provenance.")
        if self.source_start_at is not None and (
            self.source_end_at is None or self.source_start_at >= self.source_end_at
        ):
            raise ValueError("Source range must be ordered.")
        if self.kind == "text" and self.grammar_version is None:
            raise ValueError("Text patterns require a grammar version.")
        if self.kind != "stock_section" and (
            self.source_symbol is not None
            or self.source_interval is not None
            or self.source_start_at is not None
            or self.source_end_at is not None
        ):
            raise ValueError("Stock provenance only belongs to stock sections.")
        if self.kind != "text" and self.grammar_version is not None:
            raise ValueError("Grammar version only belongs to text patterns.")
        return self


class CanonicalPattern(PolicyModel):
    schema_version: Literal[1] = 1
    points: tuple[PatternPoint, ...] = Field(min_length=2, max_length=256)
    provenance: PatternProvenance

    @model_validator(mode="after")
    def ordered_points(self) -> "CanonicalPattern":
        if self.points[0].x != 0 or self.points[-1].x != 1:
            raise ValueError("Pattern endpoints must be exactly x=0 and x=1.")
        if any(a.x >= b.x for a, b in zip(self.points, self.points[1:], strict=False)):
            raise ValueError("Pattern x coordinates must strictly increase.")
        if self.is_flat:
            raise ValueError("A constant pattern has no measurable shape.")
        return self

    @property
    def is_flat(self) -> bool:
        # No absolute epsilon: translation/scaling of representable nonconstant
        # coordinates cannot turn a valid shape into an ineligible flat shape.
        return all(point.y == self.points[0].y for point in self.points)

    def content_hash(self) -> str:
        """SHA-256 of versioned, sorted compact UTF-8 JSON.

        Point numbers become big-endian IEEE-754 binary64 hex strings, with -0
        normalized to +0. This avoids Python/JS number-rendering differences.
        Revision/acknowledgement are not content; provenance is content.
        """

        def number(value: float) -> str:
            return struct.pack(">d", 0.0 if value == 0 else value).hex()

        payload = {
            "schema_version": self.schema_version,
            "points": [{"x": number(p.x), "y": number(p.y)} for p in self.points],
            "provenance": self.provenance.model_dump(mode="json"),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class Revisions(PolicyModel):
    pattern_revision: int = Field(ge=1, strict=True)
    settings_revision: int = Field(ge=1, strict=True)


class PreviewAcknowledgement(PolicyModel):
    pattern_revision: int = Field(ge=1, strict=True)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class DurationBounds(PolicyModel):
    minimum_candles: int = Field(default=20, ge=8, le=240, strict=True)
    maximum_candles: int = Field(default=120, ge=8, le=240, strict=True)

    @model_validator(mode="after")
    def ordered(self) -> "DurationBounds":
        if self.minimum_candles > self.maximum_candles:
            raise ValueError("Minimum duration exceeds maximum duration.")
        return self


class CurrentScanSettings(PolicyModel):
    interval: Literal["1min", "5min", "15min", "30min", "1h", "1day"] = "1day"
    duration: DurationBounds = Field(default_factory=DurationBounds)
    minimum_completion: float = Field(default=0.5, gt=0, le=1, strict=True)
    minimum_movement_percent: float = Field(default=5, ge=0, strict=True)
    minimum_similarity: float = Field(default=80, ge=0, le=100, strict=True)
    match_type: Literal["all", "full", "forming"] = "all"
    session: Literal["regular"] = "regular"
    include_unfinished: bool = Field(default=False, strict=True)
    top_n: int = Field(default=20, ge=1, le=100, strict=True)

    @model_validator(mode="after")
    def achievable_type(self) -> "CurrentScanSettings":
        if self.match_type == "forming":
            largest = self.duration.maximum_candles
            if largest < 9 or Fraction(str(self.minimum_completion)) > Fraction(
                largest - 2, largest - 1
            ):
                raise ValueError("No forming completion is achievable within the duration bounds.")
        return self


class CurrentUniverse(PolicyModel):
    kind: UniverseKind = UniverseKind.US_EQUITIES
    symbols: tuple[str, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def valid_selection(self) -> "CurrentUniverse":
        if self.kind is UniverseKind.CUSTOM:
            if not self.symbols or len(set(self.symbols)) != len(self.symbols):
                raise ValueError("Custom symbols must be nonempty and unique.")
            if any(re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,14}", s) is None for s in self.symbols):
                raise ValueError("Custom symbols must use normalized supported ticker syntax.")
        elif self.symbols:
            raise ValueError("Named universes cannot contain explicit symbols.")
        return self


class CurrentScanRequest(PolicyModel):
    pattern: CanonicalPattern
    revisions: Revisions
    preview_acknowledgement: PreviewAcknowledgement | None = None
    settings: CurrentScanSettings = Field(default_factory=CurrentScanSettings)
    universe: CurrentUniverse = Field(default_factory=CurrentUniverse)

    @model_validator(mode="after")
    def accepted_content(self) -> "CurrentScanRequest":
        ack = self.preview_acknowledgement
        if self.pattern.provenance.kind in ("text", "screenshot_trace") and ack is None:
            raise ValueError("Text and traced screenshots require preview acknowledgement.")
        if ack is not None and (
            ack.pattern_revision != self.revisions.pattern_revision
            or ack.content_hash != self.pattern.content_hash()
        ):
            raise ValueError(
                "Preview acknowledgement does not match this pattern revision/content."
            )
        return self


def completion_fraction(observed_candles: int, complete_duration_candles: int) -> Fraction:
    if (
        type(observed_candles) is not int
        or type(complete_duration_candles) is not int
        or not 8 <= observed_candles <= complete_duration_candles <= 240
    ):
        raise ValueError("Completion requires 8 <= observed <= complete <= 240 candles.")
    return Fraction(observed_candles - 1, complete_duration_candles - 1)


class Coverage(PolicyModel):
    universe_total: int = Field(ge=0, strict=True)
    scanned: int = Field(ge=0, strict=True)
    eligible: int = Field(ge=0, strict=True)
    stale: int = Field(ge=0, strict=True)
    skipped: int = Field(ge=0, strict=True)
    failed: int = Field(ge=0, strict=True)
    not_evaluated: int = Field(ge=0, strict=True)
    evaluation_incomplete: int = Field(default=0, ge=0, strict=True)
    matches_returned: int = Field(ge=0, strict=True)

    @model_validator(mode="after")
    def accounting(self) -> "Coverage":
        terminal = self.scanned + self.stale + self.skipped + self.failed + self.not_evaluated
        if terminal != self.universe_total:
            raise ValueError("Disjoint terminal counts must sum to universe_total.")
        if not self.matches_returned <= self.scanned <= self.eligible <= self.universe_total:
            raise ValueError("Require matches_returned <= scanned <= eligible <= universe_total.")
        if self.evaluation_incomplete > self.not_evaluated:
            raise ValueError("Incomplete evaluations are a subset of not_evaluated.")
        return self


class ExecutionStatus(PolicyModel):
    partial: bool = Field(strict=True)
    stop_reasons: tuple[
        Literal[
            "provider_budget",
            "computation_budget",
            "deadline",
            "cancelled",
            "provider_error",
            "candidate_limit",
        ],
        ...,
    ] = ()
    provider_requests: int = Field(default=0, ge=0, strict=True)
    hypotheses_evaluated: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def coherent_stop(self) -> "ExecutionStatus":
        if self.stop_reasons and not self.partial:
            raise ValueError("A stopped scan must be marked partial.")
        if len(set(self.stop_reasons)) != len(self.stop_reasons):
            raise ValueError("Stop reasons must be unique.")
        return self


class ShapeComponents(PolicyModel):
    shape: float = Field(ge=0, le=100, strict=True)
    direction: float = Field(ge=0, le=100, strict=True)
    error: float = Field(ge=0, le=100, strict=True)
    amplitude: None = None


class ComparisonObservation(PolicyModel):
    timestamp: AwareDatetime
    close: str = Field(min_length=1, max_length=128)
    open: str | None = Field(default=None, min_length=1, max_length=128)
    high: str | None = Field(default=None, min_length=1, max_length=128)
    low: str | None = Field(default=None, min_length=1, max_length=128)
    volume: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("close", "open", "high", "low", "volume")
    @classmethod
    def finite_value(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        try:
            number = Decimal(value)
        except InvalidOperation as error:
            raise ValueError("Comparison values must be decimal strings.") from error
        if info.field_name == "volume":
            if not number.is_finite() or number < 0:
                raise ValueError("Comparison volume must be finite and non-negative.")
            return value
        if not number.is_finite() or number <= 0:
            raise ValueError("Comparison prices must be finite and positive.")
        return value

    @model_validator(mode="after")
    def complete_candle(self) -> "ComparisonObservation":
        fields = (self.open, self.high, self.low, self.volume)
        if all(value is None for value in fields):
            return self
        if self.open is None or self.high is None or self.low is None or self.volume is None:
            raise ValueError("An OHLCV observation requires all candle values.")
        opening, close = Decimal(self.open), Decimal(self.close)
        if Decimal(self.low) > min(opening, close) or Decimal(self.high) < max(opening, close):
            raise ValueError("Comparison candle high and low must bound its prices.")
        return self


class MatchComparison(PolicyModel):
    observations: tuple[ComparisonObservation, ...] = Field(min_length=8, max_length=240)
    log_relative_closes: tuple[float, ...] = Field(min_length=8, max_length=240)

    @model_validator(mode="after")
    def coherent_path(self) -> "MatchComparison":
        if len(self.observations) != len(self.log_relative_closes):
            raise ValueError("Comparison paths must have equal observation counts.")
        if self.log_relative_closes[0] != 0:
            raise ValueError("Log-relative prices must start at zero.")
        if any(
            a.timestamp >= b.timestamp
            for a, b in zip(self.observations, self.observations[1:], strict=False)
        ):
            raise ValueError("Comparison timestamps must strictly increase.")
        return self


class CurrentPatternMatch(PolicyModel):
    symbol: str = Field(min_length=1, max_length=32)
    similarity_score: float = Field(ge=0, le=100, strict=True)
    components: ShapeComponents
    complete_duration_candles: int = Field(ge=8, le=240, strict=True)
    observed_candles: int = Field(ge=8, le=240, strict=True)
    movement_percent: float = Field(ge=0, strict=True)
    start_at: AwareDatetime
    freshness: Freshness
    comparison: MatchComparison | None = None
    matched_start_session: date | None = None
    last_candle_session: date | None = None

    @model_validator(mode="after")
    def current_alignment(self) -> "CurrentPatternMatch":
        completion_fraction(self.observed_candles, self.complete_duration_candles)
        if self.freshness.status != "current":
            raise ValueError("Excluded candles cannot appear in ranked matches.")
        if self.freshness.expected is None or self.start_at >= self.freshness.expected.open_at:
            raise ValueError("An alignment must begin before its last candle.")
        if self.last_candle_session is not None or self.matched_start_session is not None:
            expected = self.freshness.expected
            if (
                expected.interval is not BarInterval.ONE_DAY
                or self.last_candle_session != expected.session_date
                or self.matched_start_session
                != self.start_at.astimezone(ZoneInfo(expected.timezone)).date()
            ):
                raise ValueError("Matched sessions must identify the daily alignment.")
        if self.comparison is not None and (
            len(self.comparison.observations) != self.observed_candles
            or self.comparison.observations[0].timestamp != self.start_at
            or self.comparison.observations[-1].timestamp != self.freshness.expected.open_at
        ):
            raise ValueError("Comparison samples must identify the exact matched suffix.")
        return self

    @computed_field  # type: ignore[prop-decorator]  # Pydantic property decorator support in mypy.
    @property
    def estimated_completion(self) -> float:
        return float(completion_fraction(self.observed_candles, self.complete_duration_candles))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def match_type(self) -> Literal["full", "forming"]:
        return "full" if self.observed_candles == self.complete_duration_candles else "forming"


class CurrentScanResult(PolicyModel):
    scan_id: str = Field(min_length=1)
    revisions: Revisions
    cutoff: FrozenCutoff
    pattern_content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    scoring_version: Literal["current_shape_v1"] = "current_shape_v1"
    settings: CurrentScanSettings
    matches: tuple[CurrentPatternMatch, ...]
    coverage: Coverage
    execution: ExecutionStatus

    @model_validator(mode="after")
    def consistent_results(self) -> "CurrentScanResult":
        if len({match.symbol for match in self.matches}) != len(self.matches):
            raise ValueError("Each stock may appear once.")
        if len(self.matches) != self.coverage.matches_returned:
            raise ValueError("Match count differs from coverage.")
        if len(self.matches) > self.settings.top_n:
            raise ValueError("Result exceeds top_n.")
        if self.coverage.scanned < self.coverage.universe_total and not self.execution.partial:
            raise ValueError("Incomplete coverage must be labelled partial.")
        for match in self.matches:
            settings = self.settings
            if (
                match.freshness.cutoff_at != self.cutoff.cutoff_at
                or match.freshness.expected is None
                or match.freshness.expected.interval.value != settings.interval
                or (not settings.include_unfinished and not match.freshness.is_complete)
                or match.similarity_score < settings.minimum_similarity
                or match.movement_percent < settings.minimum_movement_percent
                or not settings.duration.minimum_candles
                <= match.complete_duration_candles
                <= settings.duration.maximum_candles
                or completion_fraction(match.observed_candles, match.complete_duration_candles)
                < Fraction(str(settings.minimum_completion))
                or settings.match_type not in ("all", match.match_type)
            ):
                raise ValueError("Result alignment violates submitted settings.")
        return self
