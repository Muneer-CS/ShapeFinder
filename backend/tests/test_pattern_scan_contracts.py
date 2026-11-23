from fractions import Fraction
from typing import Any

import pytest
from pydantic import ValidationError

from shape_finder.api.current_scan_schemas import (
    CurrentPatternScanRequest,
    CurrentPatternScanResponse,
)
from shape_finder.core.candle_eligibility import (
    CachedCandle,
    CandleIdentity,
    Freshness,
)
from shape_finder.core.pattern_scan import (
    CanonicalPattern,
    Coverage,
    CurrentScanSettings,
    CurrentUniverse,
    ExecutionStatus,
    completion_fraction,
)


def result_payload() -> dict[str, Any]:
    identity = CandleIdentity.model_validate(
        {
            "interval": "1day",
            "calendar_id": "fixture-US",
            "session_date": "2025-03-10",
            "timezone": "America/New_York",
            "open_at": "2025-03-10T13:30:00Z",
            "close_at": "2025-03-10T20:00:00Z",
        }
    )
    fresh = Freshness.model_validate(
        {
            "cutoff_at": "2025-03-10T20:03:00Z",
            "status": "current",
            "expected": identity,
            "retrieved_at": "2025-03-10T20:03:00Z",
            "is_complete": True,
            "publishable_at": "2025-03-10T20:01:00Z",
            "finalizable_at": "2025-03-10T20:02:00Z",
            "calendar_version": "fixture-1",
        }
    )
    # Model validation exercises both the domain object and future API response.
    assert CachedCandle(identity=identity, retrieved_at=fresh.retrieved_at, finalized=True)
    return {
        "scan_id": "fixture",
        "pattern_content_hash": pattern().content_hash(),
        "revisions": {"pattern_revision": 1, "settings_revision": 1},
        "cutoff": {"cutoff_at": fresh.cutoff_at},
        "settings": {},
        "matches": [
            {
                "symbol": "AAPL",
                "similarity_score": 87.2,
                "components": {"shape": 90, "direction": 85, "error": 80},
                "complete_duration_candles": 41,
                "observed_candles": 21,
                "movement_percent": 8.4,
                "start_at": "2025-02-01T14:30:00Z",
                "freshness": fresh.model_dump(),
            }
        ],
        "coverage": {
            "universe_total": 10,
            "scanned": 5,
            "eligible": 5,
            "stale": 1,
            "skipped": 1,
            "failed": 0,
            "not_evaluated": 3,
            "matches_returned": 1,
        },
        "execution": {"partial": True, "stop_reasons": ["computation_budget"]},
    }


def test_result_computed_fields_and_response_schema() -> None:
    r = CurrentPatternScanResponse.model_validate(result_payload())
    wire = r.model_dump(mode="json")
    assert wire["matches"][0]["estimated_completion"] == 0.5
    assert wire["matches"][0]["match_type"] == "forming"
    assert wire["matches"][0]["components"]["amplitude"] is None
    assert "estimated_completion" in str(
        CurrentPatternScanResponse.model_json_schema(mode="serialization")
    )


@pytest.mark.parametrize(
    "change",
    [
        "duplicate",
        "filter",
        "threshold",
        "completion",
        "movement",
        "duration",
        "cutoff",
        "interval",
        "coverage",
        "partial",
    ],
)
def test_result_rejects_inconsistent_measurements(change: str) -> None:
    p = result_payload()
    if change == "duplicate":
        p["matches"] *= 2
    elif change == "filter":
        p["settings"]["match_type"] = "full"
    elif change == "threshold":
        p["settings"]["minimum_similarity"] = 90
    elif change == "completion":
        p["matches"][0]["observed_candles"] = 20
    elif change == "movement":
        p["matches"][0]["movement_percent"] = 4
    elif change == "duration":
        p["matches"][0]["complete_duration_candles"] = 19
    elif change == "cutoff":
        p["cutoff"]["cutoff_at"] = "2025-03-11T20:03:00Z"
    elif change == "interval":
        p["settings"]["interval"] = "5min"
    elif change == "coverage":
        p["coverage"]["matches_returned"] = 0
    elif change == "partial":
        p["execution"] = {"partial": False}
    with pytest.raises(ValidationError):
        CurrentPatternScanResponse.model_validate(p)


def pattern(**overrides: Any) -> CanonicalPattern:
    return CanonicalPattern.model_validate(
        {
            "points": [{"x": 0, "y": 0}, {"x": 0.5, "y": -1}, {"x": 1, "y": 1}],
            "provenance": {"kind": "drawing"},
            **overrides,
        }
    )


@pytest.mark.parametrize(
    "points",
    [
        [{"x": 0, "y": 1}],
        [{"x": 0.1, "y": 0}, {"x": 1, "y": 1}],
        [{"x": 0, "y": 0}, {"x": 0.9, "y": 1}],
        [{"x": 0, "y": 0}, {"x": 0, "y": 1}, {"x": 1, "y": 1}],
        [{"x": 0, "y": 0}, {"x": 0.8, "y": 1}, {"x": 0.2, "y": 2}, {"x": 1, "y": 1}],
        [{"x": 0, "y": 1}, {"x": 1, "y": 1}],
        [{"x": False, "y": 0}, {"x": 1, "y": 1}],
        [{"x": 0, "y": float("nan")}, {"x": 1, "y": 1}],
        [{"x": 0, "y": 0}, {"x": 1, "y": float("inf")}],
        [{"x": 0, "y": -1001}, {"x": 1, "y": 1}],
    ],
)
def test_rejects_invalid_points(points: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        pattern(points=points)


def test_coordinate_bounds_flatness_and_affine_eligibility() -> None:
    for scale, offset in [(1, 0), (0.000000001, 0), (0.1, 500), (500, -500)]:
        p = pattern(points=[{"x": 0, "y": offset}, {"x": 1, "y": offset + scale}])
        assert not p.is_flat
    assert pattern(points=[{"x": 0, "y": -1000}, {"x": 1, "y": 1000}])
    with pytest.raises(ValidationError):
        pattern(points=[{"x": i / 256, "y": i} for i in range(257)])


def test_hash_is_deterministic_not_a_shape_equivalence_hash() -> None:
    p = pattern()
    assert p.content_hash() == "92f8edef3516bcb32ffd9f479d05efa98000a56323dee44d3a2b0abc87089311"
    assert pattern().content_hash() == p.content_hash()
    assert (
        CanonicalPattern.model_validate_json(p.model_dump_json()).content_hash() == p.content_hash()
    )
    negative_zero = pattern(points=[{"x": -0.0, "y": -0.0}, {"x": 1, "y": 1}])
    positive_zero = pattern(points=[{"x": 0, "y": 0}, {"x": 1, "y": 1}])
    assert negative_zero.content_hash() == positive_zero.content_hash()
    assert pattern(points=[{"x": 0, "y": 1}, {"x": 1, "y": 2}]).content_hash() != p.content_hash()
    assert (
        pattern(provenance={"kind": "text", "grammar_version": 1}).content_hash()
        != p.content_hash()
    )


def test_acknowledgement_content_revision_and_settings_revision() -> None:
    p = pattern(provenance={"kind": "text", "grammar_version": 1})
    payload: dict[str, Any] = {
        "pattern": p.model_dump(),
        "revisions": {"pattern_revision": 2, "settings_revision": 1},
    }
    with pytest.raises(ValidationError, match="acknowledgement"):
        CurrentPatternScanRequest.model_validate(payload)
    payload["preview_acknowledgement"] = {"pattern_revision": 2, "content_hash": p.content_hash()}
    request = CurrentPatternScanRequest.model_validate(payload)
    assert request.settings.minimum_similarity == 80
    payload["revisions"]["settings_revision"] = 2
    assert CurrentPatternScanRequest.model_validate(
        payload
    )  # Settings do not alter pattern acceptance.
    payload["revisions"]["pattern_revision"] = 3
    with pytest.raises(ValidationError, match="revision/content"):
        CurrentPatternScanRequest.model_validate(payload)
    payload["revisions"]["pattern_revision"] = 2
    payload["pattern"]["points"][1]["y"] = -0.5
    with pytest.raises(ValidationError, match="revision/content"):
        CurrentPatternScanRequest.model_validate(payload)


def test_screenshot_acknowledgement_and_stock_provenance() -> None:
    with pytest.raises(ValidationError):
        CurrentPatternScanRequest.model_validate(
            {
                "pattern": pattern(provenance={"kind": "screenshot_trace"}),
                "revisions": {"pattern_revision": 1, "settings_revision": 1},
            }
        )
    with pytest.raises(ValidationError):
        pattern(provenance={"kind": "stock_section"})
    assert pattern(
        provenance={
            "kind": "stock_section",
            "source_symbol": "NVDA",
            "source_interval": "1day",
            "source_start_at": "2025-01-02T14:30:00Z",
            "source_end_at": "2025-01-03T21:00:00Z",
        }
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"minimum_similarity": None},
        {"minimum_similarity": -1},
        {"minimum_similarity": 101},
        {"minimum_similarity": float("nan")},
        {"minimum_completion": 0},
        {"minimum_completion": 1.1},
        {"minimum_movement_percent": -1},
        {"duration": {"minimum_candles": 121, "maximum_candles": 120}},
        {"duration": {"minimum_candles": 7}},
        {"interval": "1week"},
        {"match_type": "forming", "minimum_completion": 1},
        {"session": "extended"},
        {"include_unfinished": 1},
        {"top_n": True},
    ],
)
def test_settings_reject_invalid_or_incompatible_values(settings: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        CurrentScanSettings.model_validate(settings)


def test_defaults_zero_and_output_schema() -> None:
    s = CurrentScanSettings(minimum_similarity=0)
    assert s.minimum_similarity == 0
    assert s.duration.minimum_candles == 20 and s.duration.maximum_candles == 120
    assert s.minimum_completion == 0.5 and s.minimum_movement_percent == 5
    assert not s.include_unfinished
    assert "settings" in CurrentPatternScanRequest.model_json_schema()["properties"]


@pytest.mark.parametrize(
    "observed,complete,expected",
    [
        (11, 21, Fraction(1, 2)),
        (10, 21, Fraction(9, 20)),
        (10, 20, Fraction(9, 19)),
        (11, 20, Fraction(10, 19)),
        (21, 41, Fraction(1, 2)),
        (41, 41, Fraction(1)),
    ],
)
def test_exact_completion(observed: int, complete: int, expected: Fraction) -> None:
    assert completion_fraction(observed, complete) == expected


@pytest.mark.parametrize("observed,complete", [(7, 20), (21, 20), (20, 241), (True, 20)])
def test_invalid_completion(observed: int, complete: int) -> None:
    with pytest.raises(ValueError):
        completion_fraction(observed, complete)


def test_coverage_terminal_partition_and_eligible_overlap() -> None:
    valid = dict(
        universe_total=1000,
        scanned=250,
        eligible=250,
        stale=10,
        skipped=20,
        failed=5,
        not_evaluated=715,
        matches_returned=1,
    )
    assert Coverage.model_validate(valid)
    for changes in (
        {"eligible": 235},
        {"scanned": 251},
        {"matches_returned": 251},
        {"evaluation_incomplete": 716},
        {"failed": -1},
        {"eligible": 1001},
    ):
        with pytest.raises(ValidationError):
            Coverage.model_validate({**valid, **changes})


def test_execution_and_universe_validation() -> None:
    assert ExecutionStatus(partial=True, stop_reasons=("computation_budget",))
    with pytest.raises(ValidationError):
        ExecutionStatus(partial=False, stop_reasons=("deadline",))
    assert CurrentUniverse(kind="custom", symbols=("AAPL", "BRK.B"))
    for value in (
        {"kind": "custom"},
        {"kind": "nasdaq", "symbols": ["AAPL"]},
        {"kind": "custom", "symbols": ["A/B"]},
    ):
        with pytest.raises(ValidationError):
            CurrentUniverse.model_validate(value)
