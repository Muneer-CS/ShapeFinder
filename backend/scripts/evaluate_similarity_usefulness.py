"""Evaluate the practical meaning of ShapeFinder scores using cached real data.

This is a read-only, dev-only analysis utility. It never constructs a provider and
therefore cannot consume market-data quota or hydrate the cache.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import html
import json
import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from shape_finder.application.similarity_engine import ChartSimilarityEngine
from shape_finder.application.similarity_search import HistoricalSimilarityScanner
from shape_finder.core.market_data import BarInterval, TimeSeries
from shape_finder.core.similarity_search import SimilaritySearchQuery
from shape_finder.infrastructure.persistence.sqlite_market_data import SQLiteMarketDataRepository

BandName = Literal["95-100", "90-95", "85-90", "80-85", "75-80", "70-75", "below-70"]
Label = Literal["Strong", "Useful", "Weak", "Misleading"]


@dataclass(frozen=True, slots=True)
class ReferenceCase:
    id: str
    pattern: str
    symbol: str
    start: str
    end: str


REFERENCE_CASES = (
    ReferenceCase("steady-uptrend", "strong steady uptrend", "MSFT", "2023-11-01", "2024-01-31"),
    ReferenceCase("strong-downtrend", "strong downtrend", "NVDA", "2022-08-15", "2022-10-14"),
    ReferenceCase("v-reversal", "V-shaped reversal", "AAPL", "2020-02-19", "2020-06-08"),
    ReferenceCase(
        "peak-and-fall", "inverted-V / peak-and-fall", "NVDA", "2021-10-04", "2022-01-27"
    ),
    ReferenceCase("sideways-choppy", "sideways / choppy", "AAPL", "2021-01-04", "2021-04-01"),
    ReferenceCase(
        "volatile-pullbacks",
        "volatile trend with pullbacks",
        "NVDA",
        "2023-01-03",
        "2023-06-30",
    ),
    ReferenceCase("short-window", "short reference window", "AMD", "2024-01-16", "2024-01-26"),
    ReferenceCase("long-window", "long reference window", "XOM", "2021-01-04", "2022-01-03"),
)

# Fixed after reviewing the generated rebased overlays for this cache snapshot.
# These practical human judgments are guided by the eight recorded criteria, not a
# second scoring formula. New cache samples fall back to the rule-aided label.
REVIEWER_LABELS: dict[tuple[str, str, str], Label] = {
    ("short-window", "MSFT", "2024-01-16"): "Useful",
    ("strong-downtrend", "AMD", "2022-08-15"): "Strong",
    ("peak-and-fall", "AMD", "2021-10-04"): "Strong",
    ("v-reversal", "MSFT", "2020-02-19"): "Strong",
    ("volatile-pullbacks", "AMD", "2023-01-03"): "Useful",
    ("short-window", "NVDA", "2023-06-09"): "Useful",
    ("volatile-pullbacks", "AAPL", "2023-01-03"): "Useful",
    ("v-reversal", "NVDA", "2020-02-19"): "Strong",
    ("strong-downtrend", "AAPL", "2022-08-15"): "Useful",
    ("steady-uptrend", "NVDA", "2023-10-27"): "Useful",
    ("volatile-pullbacks", "AAPL", "2019-08-23"): "Weak",
    ("strong-downtrend", "JPM", "2020-02-19"): "Weak",
    ("peak-and-fall", "MSFT", "2021-10-04"): "Useful",
    ("steady-uptrend", "NVDA", "2021-06-14"): "Useful",
    ("long-window", "JPM", "2021-01-04"): "Useful",
    ("steady-uptrend", "MSFT", "2019-10-21"): "Weak",
    ("sideways-choppy", "XOM", "2020-05-15"): "Weak",
    ("long-window", "JPM", "2022-09-22"): "Weak",
    ("peak-and-fall", "AAPL", "2019-11-19"): "Weak",
    ("strong-downtrend", "NVDA", "2021-12-23"): "Weak",
    ("steady-uptrend", "JPM", "2020-09-01"): "Weak",
    ("sideways-choppy", "XOM", "2020-01-21"): "Misleading",
    ("volatile-pullbacks", "AMD", "2022-10-04"): "Weak",
    ("strong-downtrend", "AAPL", "2023-07-18"): "Misleading",
    ("v-reversal", "NVDA", "2023-09-26"): "Weak",
}

SEARCH_START = datetime(2018, 1, 1, tzinfo=UTC)
SEARCH_END = datetime(2024, 3, 29, 23, 59, 59, tzinfo=UTC)
CANDIDATE_SYMBOLS = ("AAPL", "AMD", "JPM", "MSFT", "NVDA", "XOM")


@dataclass(frozen=True, slots=True)
class StructuralReview:
    major_turns_align: bool
    overall_direction_aligns: bool
    turning_timing_aligns: bool
    recognizable_shape: bool
    not_merely_generic_trend: bool
    comparable_amplitude: bool
    no_obvious_contradiction: bool
    rebased_overlay_convincing: bool
    points: int
    label: Label
    reference_turns: int
    candidate_turns: int
    aligned_turns: int
    mean_turn_timing_error: float | None
    rebased_overlay_rmse: float


def score_band(score: float) -> BandName:
    """Assign a displayed score to the study's half-open review bands."""
    if score >= 95:
        return "95-100"
    if score >= 90:
        return "90-95"
    if score >= 85:
        return "85-90"
    if score >= 80:
        return "80-85"
    if score >= 75:
        return "75-80"
    if score >= 70:
        return "70-75"
    return "below-70"


def qualitative_label(points: int) -> Label:
    """Map the eight-question practical rubric to a qualitative label."""
    if points >= 7:
        return "Strong"
    if points >= 5:
        return "Useful"
    if points >= 3:
        return "Weak"
    return "Misleading"


def band_counts_from_thresholds(
    windows_evaluated: int, passing: dict[str, int]
) -> dict[BandName, int]:
    """Convert cumulative threshold survival into mutually exclusive score bands."""
    return {
        "95-100": passing["95"],
        "90-95": passing["90"] - passing["95"],
        "85-90": passing["85"] - passing["90"],
        "80-85": passing["80"] - passing["85"],
        "75-80": passing["75"] - passing["80"],
        "70-75": passing["70"] - passing["75"],
        "below-70": windows_evaluated - passing["70"],
    }


def _resample(values: Sequence[float], points: int = 64) -> list[float]:
    if len(values) < 2:
        raise ValueError("At least two values are required.")
    last = len(values) - 1
    result: list[float] = []
    for index in range(points):
        position = index * last / (points - 1)
        left = math.floor(position)
        right = min(left + 1, last)
        fraction = position - left
        result.append(values[left] * (1 - fraction) + values[right] * fraction)
    return result


def _log_path(closes: Sequence[float]) -> list[float]:
    if len(closes) < 2 or any(not math.isfinite(value) or value <= 0 for value in closes):
        raise ValueError("Closes must contain at least two finite positive values.")
    first = math.log(closes[0])
    return _resample([math.log(value) - first for value in closes])


def _smooth(values: Sequence[float], radius: int = 2) -> list[float]:
    return [
        math.fsum(values[max(0, index - radius) : index + radius + 1])
        / len(values[max(0, index - radius) : index + radius + 1])
        for index in range(len(values))
    ]


def _major_turns(values: Sequence[float]) -> list[tuple[int, str, float]]:
    """Find prominent, well-separated peaks/troughs on a 64-point path."""
    smooth = _smooth(values)
    span = max(smooth) - min(smooth)
    if span <= 1e-12:
        return []
    lookaround = 6
    candidates: list[tuple[int, str, float]] = []
    for index in range(lookaround, len(smooth) - lookaround):
        left = smooth[index - lookaround : index]
        right = smooth[index + 1 : index + lookaround + 1]
        current = smooth[index]
        peak_prominence = min(current - min(left), current - min(right))
        trough_prominence = min(max(left) - current, max(right) - current)
        if current >= max((*left[-2:], *right[:2])) and peak_prominence >= 0.08 * span:
            candidates.append((index, "peak", peak_prominence))
        elif current <= min((*left[-2:], *right[:2])) and trough_prominence >= 0.08 * span:
            candidates.append((index, "trough", trough_prominence))

    selected: list[tuple[int, str, float]] = []
    for candidate in sorted(candidates, key=lambda item: (-item[2], item[0])):
        if all(abs(candidate[0] - existing[0]) >= 7 for existing in selected):
            selected.append(candidate)
    return sorted(selected, key=lambda item: item[0])


def _align_turns(
    reference: Sequence[tuple[int, str, float]],
    candidate: Sequence[tuple[int, str, float]],
    tolerance: int = 9,
) -> tuple[int, float | None]:
    used: set[int] = set()
    errors: list[float] = []
    for ref_index, ref_kind, _ in reference:
        choices = [
            (abs(ref_index - item[0]), position)
            for position, item in enumerate(candidate)
            if position not in used
            and item[1] == ref_kind
            and abs(ref_index - item[0]) <= tolerance
        ]
        if not choices:
            continue
        distance, position = min(choices)
        used.add(position)
        errors.append(distance / 63)
    return len(errors), (math.fsum(errors) / len(errors) if errors else None)


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    left_mean = math.fsum(left) / len(left)
    right_mean = math.fsum(right) / len(right)
    a = [value - left_mean for value in left]
    b = [value - right_mean for value in right]
    denominator = math.sqrt(
        math.fsum(value * value for value in a) * math.fsum(value * value for value in b)
    )
    return (
        0.0
        if denominator <= 1e-12
        else math.fsum(x * y for x, y in zip(a, b, strict=True)) / denominator
    )


def _unit_range(values: Sequence[float]) -> list[float]:
    low, high = min(values), max(values)
    span = high - low
    return [0.5] * len(values) if span <= 1e-12 else [(value - low) / span for value in values]


def review_structure(
    reference_closes: Sequence[float], candidate_closes: Sequence[float]
) -> StructuralReview:
    """Apply the documented eight-question rubric without using overall score."""
    reference = _log_path(reference_closes)
    candidate = _log_path(candidate_closes)
    reference_turns = _major_turns(reference)
    candidate_turns = _major_turns(candidate)
    aligned, timing_error = _align_turns(reference_turns, candidate_turns)
    turn_denominator = max(len(reference_turns), len(candidate_turns), 1)
    turn_ratio = aligned / turn_denominator

    reference_return = reference[-1] - reference[0]
    candidate_return = candidate[-1] - candidate[0]
    reference_span = max(reference) - min(reference)
    candidate_span = max(candidate) - min(candidate)
    return_noise = 0.08 * max(reference_span, candidate_span, 1e-12)
    direction_aligns = (
        abs(reference_return) <= return_noise and abs(candidate_return) <= return_noise
    ) or reference_return * candidate_return > 0

    reference_tail = reference[-1] - reference[-17]
    candidate_tail = candidate[-1] - candidate[-17]
    tail_aligns = abs(reference_tail) <= return_noise and abs(candidate_tail) <= return_noise
    tail_aligns = tail_aligns or reference_tail * candidate_tail > 0
    correlation = _pearson(reference, candidate)
    overlay_left, overlay_right = _unit_range(reference), _unit_range(candidate)
    overlay_rmse = math.sqrt(
        math.fsum(
            (left - right) ** 2 for left, right in zip(overlay_left, overlay_right, strict=True)
        )
        / len(overlay_left)
    )
    amplitude_ratio = min(reference_span, candidate_span) / max(reference_span, candidate_span)

    criteria = (
        turn_ratio >= 0.60 or (not reference_turns and not candidate_turns),
        direction_aligns,
        timing_error is not None and turn_ratio >= 0.60 and timing_error <= 0.10,
        correlation >= 0.70,
        len(reference_turns) >= 2 and turn_ratio >= 0.50,
        amplitude_ratio >= 0.55,
        direction_aligns and tail_aligns,
        overlay_rmse <= 0.16,
    )
    points = sum(criteria)
    return StructuralReview(
        major_turns_align=criteria[0],
        overall_direction_aligns=criteria[1],
        turning_timing_aligns=criteria[2],
        recognizable_shape=criteria[3],
        not_merely_generic_trend=criteria[4],
        comparable_amplitude=criteria[5],
        no_obvious_contradiction=criteria[6],
        rebased_overlay_convincing=criteria[7],
        points=points,
        label=qualitative_label(points),
        reference_turns=len(reference_turns),
        candidate_turns=len(candidate_turns),
        aligned_turns=aligned,
        mean_turn_timing_error=round(timing_error, 6) if timing_error is not None else None,
        rebased_overlay_rmse=round(overlay_rmse, 6),
    )


def stratified_sample(records: Sequence[dict[str, Any]], per_band: int = 5) -> list[dict[str, Any]]:
    """Select deterministic band samples while spreading references and symbols."""
    order: tuple[BandName, ...] = (
        "95-100",
        "90-95",
        "85-90",
        "80-85",
        "75-80",
        "70-75",
        "below-70",
    )
    centers = {
        "95-100": 97.5,
        "90-95": 92.5,
        "85-90": 87.5,
        "80-85": 82.5,
        "75-80": 77.5,
        "70-75": 72.5,
        "below-70": 65.0,
    }
    selected: list[dict[str, Any]] = []
    for band in order:
        candidates = sorted(
            (record for record in records if record["band"] == band),
            key=lambda record: (
                abs(float(record["overall_score"]) - centers[band]),
                record["reference_id"],
                record["symbol"],
                record["candidate_start"],
            ),
        )
        used_references: set[str] = set()
        used_symbols: set[str] = set()
        band_selection: list[dict[str, Any]] = []
        while candidates and len(band_selection) < per_band:
            best = min(
                candidates,
                key=lambda record: (
                    record["reference_id"] in used_references,
                    record["symbol"] in used_symbols,
                    abs(float(record["overall_score"]) - centers[band]),
                    record["reference_id"],
                    record["candidate_start"],
                ),
            )
            candidates.remove(best)
            band_selection.append(best)
            used_references.add(str(best["reference_id"]))
            used_symbols.add(str(best["symbol"]))
        selected.extend(band_selection)
    return selected


def _date(value: str, *, end: bool = False) -> datetime:
    parsed = datetime.fromisoformat(value).replace(tzinfo=UTC)
    return parsed.replace(hour=23, minute=59, second=59) if end else parsed


def _closes(series: TimeSeries) -> list[float]:
    return [float(bar.close) for bar in series.bars]


async def evaluate(database: Path) -> dict[str, Any]:
    repository = SQLiteMarketDataRepository(database)
    engine = ChartSimilarityEngine()
    scanner = HistoricalSimilarityScanner(engine)
    all_candidates = {
        symbol: await repository.get_time_series(
            symbol, BarInterval.ONE_DAY, SEARCH_START, SEARCH_END
        )
        for symbol in CANDIDATE_SYMBOLS
    }
    records: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    threshold_survival: list[dict[str, Any]] = []
    for case in REFERENCE_CASES:
        reference = await repository.get_time_series(
            case.symbol, BarInterval.ONE_DAY, _date(case.start), _date(case.end, end=True)
        )
        if len(reference.bars) < 2:
            raise RuntimeError(f"Reference {case.id} is absent from the local cache.")
        result = scanner.scan(
            reference,
            list(all_candidates.values()),
            SimilaritySearchQuery(
                reference_symbol=case.symbol,
                reference_start=_date(case.start),
                reference_end=_date(case.end, end=True),
                interval=BarInterval.ONE_DAY,
                search_start=SEARCH_START,
                search_end=SEARCH_END,
                candidate_symbols=CANDIDATE_SYMBOLS,
                top_n=100,
                minimum_similarity=0,
            ),
        )
        for horizon, horizon_start in (
            ("broad", SEARCH_START),
            ("recent", datetime(2022, 1, 1, tzinfo=UTC)),
        ):
            survival: dict[str, int] = {}
            evaluated = 0
            for threshold in (70.0, 75.0, 80.0, 85.0, 90.0, 95.0):
                threshold_result = scanner.scan(
                    reference,
                    list(all_candidates.values()),
                    SimilaritySearchQuery(
                        reference_symbol=case.symbol,
                        reference_start=_date(case.start),
                        reference_end=_date(case.end, end=True),
                        interval=BarInterval.ONE_DAY,
                        search_start=horizon_start,
                        search_end=SEARCH_END,
                        candidate_symbols=CANDIDATE_SYMBOLS,
                        top_n=1,
                        minimum_similarity=threshold,
                    ),
                )
                evaluated = threshold_result.statistics.windows_evaluated
                survival[str(int(threshold))] = (
                    threshold_result.statistics.windows_passing_threshold
                )
            threshold_survival.append(
                {
                    "reference_id": case.id,
                    "reference_bars": len(reference.bars),
                    "window_group": (
                        "short"
                        if len(reference.bars) <= 15
                        else "long"
                        if len(reference.bars) > 100
                        else "medium"
                    ),
                    "horizon": horizon,
                    "search_start": horizon_start.isoformat(),
                    "windows_evaluated": evaluated,
                    "passing_at_or_above": survival,
                }
            )
        reference_values = _closes(reference)
        references.append(
            {
                **asdict(case),
                "bar_count": len(reference.bars),
                "return_percent": round((reference_values[-1] / reference_values[0] - 1) * 100, 3),
                "path": [round(value, 6) for value in _unit_range(_log_path(reference_values))],
                "windows_evaluated": result.statistics.windows_evaluated,
            }
        )
        for match in result.matches:
            candidate = await repository.get_time_series(
                match.symbol, BarInterval.ONE_DAY, match.start, match.end
            )
            candidate_values = _closes(candidate)
            review = review_structure(reference_values, candidate_values)
            records.append(
                {
                    "reference_id": case.id,
                    "reference_pattern": case.pattern,
                    "reference_symbol": case.symbol,
                    "reference_start": case.start,
                    "reference_end": case.end,
                    "reference_bars": len(reference.bars),
                    "symbol": match.symbol,
                    "candidate_start": match.start.date().isoformat(),
                    "candidate_end": match.end.date().isoformat(),
                    "overall_score": match.score.overall_score,
                    "shape_score": match.score.shape_score,
                    "direction_score": match.score.direction_score,
                    "path_error_score": match.score.error_score,
                    "amplitude_score": match.score.amplitude_score,
                    "band": score_band(match.score.overall_score),
                    "review": asdict(review),
                    "reference_path": [
                        round(value, 6) for value in _unit_range(_log_path(reference_values))
                    ],
                    "candidate_path": [
                        round(value, 6) for value in _unit_range(_log_path(candidate_values))
                    ],
                }
            )

    sample = stratified_sample(records)
    for record in sample:
        review = record["review"]
        key = (record["reference_id"], record["symbol"], record["candidate_start"])
        record["qualitative_label"] = REVIEWER_LABELS.get(key, review["label"])
        record["manually_reviewed"] = key in REVIEWER_LABELS
    band_counts = Counter(record["band"] for record in records)
    broad_survival = [item for item in threshold_survival if item["horizon"] == "broad"]
    all_windows_evaluated = sum(item["windows_evaluated"] for item in broad_survival)
    aggregate_passing = {
        threshold: sum(item["passing_at_or_above"][threshold] for item in broad_survival)
        for threshold in ("70", "75", "80", "85", "90", "95")
    }
    sample_labels = defaultdict(Counter)
    for record in sample:
        sample_labels[str(record["band"])][str(record["qualitative_label"])] += 1
    return {
        "method": {
            "generated_at_utc": datetime.now(UTC).isoformat(),
            "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
            "provider_requests": 0,
            "candidate_symbols": list(CANDIDATE_SYMBOLS),
            "search_start": SEARCH_START.isoformat(),
            "search_end": SEARCH_END.isoformat(),
            "scorer": {
                "points": 64,
                "normalization": "log-relative path; linear resample; mean-center; unit RMS",
                "weights": {
                    "shape": 0.45,
                    "direction": 0.30,
                    "path_error": 0.20,
                    "amplitude": 0.05,
                },
                "flat_tolerance": 1e-8,
                "clamp": "0..100",
                "precision_decimals": 6,
            },
            "rubric": [
                "major turning points align",
                "overall direction aligns",
                "peak/trough timing aligns",
                "shape is recognizable",
                "not merely a generic trend",
                "relative amplitude is comparable",
                "no endpoint/tail contradiction",
                "rebased overlay is convincing",
            ],
            "labels": {"Strong": "7-8", "Useful": "5-6", "Weak": "3-4", "Misleading": "0-2"},
        },
        "references": references,
        "threshold_survival_by_reference_and_horizon": threshold_survival,
        "all_windows_evaluated": all_windows_evaluated,
        "all_window_band_counts": band_counts_from_thresholds(
            all_windows_evaluated, aggregate_passing
        ),
        "population_match_count": len(records),
        "population_band_counts": dict(band_counts),
        "sample_match_count": len(sample),
        "sample_label_counts_by_band": {
            band: dict(counts) for band, counts in sample_labels.items()
        },
        "sample": sample,
    }


def _svg_path(values: Sequence[float], color: str) -> str:
    points = " ".join(
        f"{16 + index * 328 / (len(values) - 1):.1f},{126 - value * 104:.1f}"
        for index, value in enumerate(values)
    )
    return f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2.2" />'


def _html_report(payload: dict[str, Any]) -> str:
    cards: list[str] = []
    for record in payload["sample"]:
        review = record["review"]
        label = record["qualitative_label"]
        header = (
            f'<header><span class="band">{html.escape(record["band"])}</span>'
            f"<strong>{record['overall_score']:.3f}</strong>"
            f'<span class="label {label.lower()}">{label} · rubric '
            f"{review['points']}/8</span></header>"
        )
        period = (
            f"<p>{html.escape(record['reference_symbol'])} "
            f"{record['reference_start']}–{record['reference_end']} · candidate "
            f"{record['candidate_start']}–{record['candidate_end']}</p>"
        )
        paths = _svg_path(record["reference_path"], "#38bdf8") + _svg_path(
            record["candidate_path"], "#fb7185"
        )
        scores = "".join(
            f"<div><dt>{name}</dt><dd>{record[key]:.1f}</dd></div>"
            for name, key in (
                ("Shape", "shape_score"),
                ("Direction", "direction_score"),
                ("Path", "path_error_score"),
                ("Amplitude", "amplitude_score"),
            )
        )
        cards.append(
            '<article class="card">'
            + header
            + f"<h2>{html.escape(record['reference_id'])} → "
            + f"{html.escape(record['symbol'])}</h2>"
            + period
            + '<svg viewBox="0 0 360 140" role="img" '
            + 'aria-label="Rebased reference and candidate overlay">'
            + '<line x1="16" y1="126" x2="344" y2="126" stroke="#334155" />'
            + paths
            + '</svg><p class="legend"><span>■ Reference</span>'
            + f"<span>■ Candidate</span></p><dl>{scores}</dl></article>"
        )
    styles = """
:root { color-scheme: dark; font-family: Inter, system-ui, sans-serif;
  background: #07111f; color: #e2e8f0; }
body { margin: 0; padding: 28px; } main { max-width: 1400px; margin: auto; }
h1 { margin-bottom: 6px; } .intro { color: #94a3b8; margin-bottom: 24px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(330px, 1fr)); gap: 16px; }
.card { background: #0f1c2e; border: 1px solid #24364d; border-radius: 14px; padding: 16px; }
header { display: flex; gap: 10px; align-items: center; } .band { color: #a5b4fc; }
header strong { font-size: 1.45rem; } .label { margin-left: auto; padding: 4px 8px;
  border-radius: 999px; background: #26364c; }
.strong { color: #86efac; } .useful { color: #fde68a; } .weak { color: #fdba74; }
.misleading { color: #fda4af; } h2 { font-size: 1rem; margin: 12px 0 4px; }
p { font-size: .82rem; color: #94a3b8; } svg { width: 100%; background: #091422;
  border-radius: 8px; } .legend { display: flex; gap: 16px; }
.legend span:first-child { color: #38bdf8; } .legend span:last-child { color: #fb7185; }
dl { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin: 0; }
dl div { background: #13243a; border-radius: 6px; padding: 7px; text-align: center; }
dt { font-size: .68rem; color: #94a3b8; }
dd { margin: 2px 0 0; font-variant-numeric: tabular-nums; }
"""
    intro = (
        f"{payload['sample_match_count']} stratified real-market matches · "
        "8 reference shapes · cached data only · blue reference, pink candidate. "
        "Labels apply the documented eight-question practical rubric, not a "
        "probability interpretation."
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>ShapeFinder score usefulness</title><style>{styles}</style></head>"
        f'<body><main><h1>ShapeFinder score usefulness</h1><p class="intro">{intro}</p>'
        f'<section class="grid">{"".join(cards)}</section></main></body></html>'
    )


def write_outputs(payload: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "evaluation.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    rows = payload["sample"]
    fields = [
        "reference_id",
        "reference_pattern",
        "reference_symbol",
        "reference_start",
        "reference_end",
        "reference_bars",
        "symbol",
        "candidate_start",
        "candidate_end",
        "overall_score",
        "shape_score",
        "direction_score",
        "path_error_score",
        "amplitude_score",
        "band",
        "qualitative_label",
        "rubric_points",
    ]
    with (output / "evaluation.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in rows:
            writer.writerow(
                {
                    **{field: record[field] for field in fields[:-2]},
                    "qualitative_label": record["qualitative_label"],
                    "rubric_points": record["review"]["points"],
                }
            )
    (output / "evaluation.html").write_text(_html_report(payload), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/shapefinder.sqlite3"))
    parser.add_argument("--output", type=Path, default=Path("validation-output/prompt6"))
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not args.database.is_file():
        raise SystemExit(f"Cached database not found: {args.database}")
    payload = asyncio.run(evaluate(args.database))
    write_outputs(payload, args.output)
    print(
        json.dumps(
            {
                key: payload[key]
                for key in (
                    "population_match_count",
                    "population_band_counts",
                    "sample_match_count",
                    "sample_label_counts_by_band",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
