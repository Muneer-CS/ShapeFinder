# Similarity score usefulness evaluation

## Decision

For the current scorer, **80 is the defensible minimum for a routinely useful match**,
**85 is strong**, and **90 should mean exceptional/very rare**. The score is an engineered
composite, not a probability.

The evidence supports presenting 80% as the recommended starting point, but it does not yet
justify silently changing the backward-compatible `Any similarity` default. The study used
six cached stocks, eight deliberately different reference shapes, and 25 visually reviewed
matches. A larger blind review should precede a default change.

The 70% preset is not useful as a quality promise. All five reviewed 70–75 examples were
weak. If retained, it should be described as broad exploration; removing it from the primary
preset list is reasonable in a later UI prompt.

## Frozen scorer

No scorer file was changed for this evaluation. The current implementation:

1. Converts closes to a log-relative path, `ln(price) - ln(first price)`.
2. Linearly resamples every path to 64 points.
3. Mean-centers the path and measures its root-mean-square amplitude.
4. Divides non-flat centered paths by RMS to create the shape; first differences of that
   shape form the direction series.
5. Computes `shape = 50 × (cosine(shape_ref, shape_candidate) + 1)`.
6. Computes `direction` with the same cosine mapping on first differences.
7. Fits a non-negative scale to the candidate shape and computes
   `path = 100 × exp(-2 × RMS(residual))`.
8. Computes `amplitude = 100 × min(RMS_ref, RMS_candidate) / max(...)`.
9. Combines `0.45 × shape + 0.30 × direction + 0.20 × path + 0.05 × amplitude`.
10. Clamps component and overall scores to 0–100 and rounds to six decimals.

The flat tolerance is `1e-8`. Two flat paths score 100 in every component; one flat and one
non-flat path score zero. Scalar and optimized batch paths continue to share this contract.

## Data and method

The read-only harness is `backend/scripts/evaluate_similarity_usefulness.py`. It opens the
local SQLite cache without constructing a provider, so this study made **zero provider
requests** and performed no hydration. The frozen database SHA-256 was
`dbf87853ccbde6afd6c648087b30578283d5c2e6d6a9b6edccbdbe18e7ab1ba2`.

References covered:

| Pattern | Reference | Bars | Return |
| --- | --- | ---: | ---: |
| Strong steady uptrend | MSFT, 2023-11-01 to 2024-01-31 | 62 | +14.9% |
| Strong downtrend | NVDA, 2022-08-15 to 2022-10-14 | 44 | -41.0% |
| V-shaped reversal | AAPL, 2020-02-19 to 2020-06-08 | 77 | +3.0% |
| Inverted-V / peak-and-fall | NVDA, 2021-10-04 to 2022-01-27 | 80 | +11.2% |
| Sideways/choppy | AAPL, 2021-01-04 to 2021-04-01 | 62 | -5.0% |
| Volatile trend with pullbacks | NVDA, 2023-01-03 to 2023-06-30 | 124 | +195.5% |
| Short window | AMD, 2024-01-16 to 2024-01-26 | 9 | +11.7% |
| Long window | XOM, 2021-01-04 to 2022-01-03 | 252 | +53.1% |

Candidate histories were AAPL, AMD, JPM, MSFT, NVDA, and XOM from 2018-01-01 through
2024-03-29. The scorer evaluated 62,916 windows. Existing self-overlap, cross-match overlap,
ranking, and top-N semantics were used unchanged. A deterministic stratifier selected five
examples per populated band, yielding 25 visual reviews. Both the rule signals and final
reviewer labels remain in the generated JSON; local JSON, CSV, and HTML overlays are written
under ignored `backend/validation-output/prompt6/`.

The practical rubric asks whether major turning points, direction, peak/trough timing,
recognizable shape, distinctiveness beyond a generic trend, relative amplitude, absence of
contradictions, and the rebased overlay are convincing. Labels are practical judgments:
Strong, Useful, Weak, or Misleading—not statistical ground truth.

## Distribution and visual findings

| Score band | All windows | Visually reviewed | Reviewer labels |
| --- | ---: | ---: | --- |
| 95–100 | 0 | 0 | Absent |
| 90–95 | 0 | 0 | Absent |
| 85–90 | 43 | 5 | 3 Strong, 2 Useful |
| 80–85 | 159 | 5 | 1 Strong, 4 Useful |
| 75–80 | 509 | 5 | 3 Useful, 2 Weak |
| 70–75 | 1,588 | 5 | 5 Weak |
| Below 70 | 60,617 | 5 | 3 Weak, 2 Misleading |

This places the practical transition near 80, not 70. The 75–80 band can surface useful
ideas but is exploratory and inconsistent. In this sample, 80–85 was uniformly useful or
strong, while 85–90 was predominantly strong. No conclusion about the visual quality of
90+ can be drawn directly because no such real window occurred; its absence makes it an
exceptional, not routinely useful, threshold.

Component means for the reviewed samples show why the overall score matters:

| Band | Shape | Direction | Path | Amplitude |
| --- | ---: | ---: | ---: | ---: |
| 85–90 | 97.8 | 91.3 | 57.3 | 70.9 |
| 80–85 | 96.9 | 84.0 | 51.9 | 54.6 |
| 75–80 | 95.6 | 73.6 | 45.3 | 62.0 |
| 70–75 | 92.0 | 68.5 | 34.0 | 74.1 |
| Below 70 | 87.4 | 60.0 | 27.1 | 45.2 |

Shape correlation stays high even in weak bands. Direction and fitted path scores provide
most of the useful separation. This confirms generic-trend inflation: a same-direction chart
can look persuasive to the shape component while missing turn timing or local path behavior.
Same-calendar cross-stock matches also benefit from common market movement and are not
always distinctive analogues.

Amplitude is a secondary weakness. Because it has only 5% weight, visually similar paths
with material volatility mismatch can still score above 80 or even 85. This is not a
correctness defect, but it deserves future product research.

## Window length and recency

Short windows inflate scores materially. For the nine-bar reference, 2.232% of windows
reached 80 and 0.451% reached 85. Across the five medium references, only 0.020% reached 80
and 0.010% reached 85—approximately 112× and 45× lower, respectively. Short-window high
scores should therefore be treated with more skepticism. Long/volatile references varied by
distinctiveness, so duration is not the only driver.

Score interpretation was stable between the broad and recent horizons. Across all eight
references, broad history produced 3.654% at 70+, 0.321% at 80+, and 0.068% at 85+; the
2022–2024 subset produced 3.646%, 0.395%, and 0.107%. Recent history was slightly richer in
high scores, but not enough to warrant date-specific thresholds. The existing search-period
control remains sufficient.

## Recommendation

- Recommended starting threshold: **80%+**.
- Strong match threshold: **85%+**.
- Exceptional/very rare threshold: **90%+**; zero of 62,916 evaluated windows reached it.
- 75%+: retain only as an explicitly exploratory option.
- 70%+: remove from the primary quality presets, or clearly label as broad exploration.
- Keep `Any similarity` as the actual default for backward compatibility until a broader,
  blind-labelled evaluation confirms that changing visible default behavior is desirable.

Future work should test more symbols and regimes, calibrate by reference length, measure
market-beta/coincident-period inflation, and examine whether amplitude and turning-point
timing deserve stronger influence. None of those changes belong in this evaluation prompt.
