from scripts.evaluate_similarity_usefulness import (
    band_counts_from_thresholds,
    qualitative_label,
    review_structure,
    score_band,
    stratified_sample,
)


def test_score_bands_are_stable_at_boundaries() -> None:
    assert [score_band(value) for value in (100, 95, 94.999, 90, 85, 80, 75, 70, 69.999)] == [
        "95-100",
        "95-100",
        "90-95",
        "90-95",
        "85-90",
        "80-85",
        "75-80",
        "70-75",
        "below-70",
    ]


def test_structural_review_is_deterministic_and_independent_of_price_level() -> None:
    reference = [100, 104, 98, 108, 105, 116, 112, 124]
    scaled = [value * 3.5 for value in reference]
    first = review_structure(reference, scaled)
    second = review_structure(reference, scaled)

    assert first == second
    assert first.rebased_overlay_rmse == 0
    assert first.overall_direction_aligns
    assert first.recognizable_shape
    assert first.label in {"Strong", "Useful"}


def test_labels_cover_the_four_documented_ranges() -> None:
    assert qualitative_label(8) == qualitative_label(7) == "Strong"
    assert qualitative_label(6) == qualitative_label(5) == "Useful"
    assert qualitative_label(4) == qualitative_label(3) == "Weak"
    assert qualitative_label(2) == qualitative_label(0) == "Misleading"


def test_cumulative_threshold_counts_convert_to_exclusive_bands() -> None:
    counts = band_counts_from_thresholds(
        1_000,
        {"70": 200, "75": 100, "80": 40, "85": 10, "90": 3, "95": 1},
    )

    assert counts == {
        "95-100": 1,
        "90-95": 2,
        "85-90": 7,
        "80-85": 30,
        "75-80": 60,
        "70-75": 100,
        "below-70": 800,
    }
    assert sum(counts.values()) == 1_000


def test_stratified_sample_is_repeatable_and_spreads_references() -> None:
    records = [
        {
            "band": "80-85",
            "overall_score": score,
            "reference_id": reference,
            "symbol": symbol,
            "candidate_start": f"2024-01-{index + 1:02d}",
        }
        for index, (score, reference, symbol) in enumerate(
            ((82.4, "a", "AAA"), (82.6, "b", "BBB"), (82.3, "a", "CCC"), (82.7, "c", "AAA"))
        )
    ]
    first = stratified_sample(records, per_band=3)

    assert first == stratified_sample(tuple(reversed(records)), per_band=3)
    assert len(first) == 3
    assert len({item["reference_id"] for item in first}) == 3
