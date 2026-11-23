# Current-pattern computation engine

This stage adds `application/current_shape.py` and `application/current_pattern_scan.py`.
It does not register routes, retrieve data, verify production provider capabilities,
install a calendar, change the historical scanner/scorer, or alter the UI. The
uncommitted foundation changes are retained.

## Input boundary

`EligibleHistory` carries a symbol, optional TimeSeries, validated Freshness, and a
calendar-derived tuple of required CandleIdentity observations. The caller must
establish this schedule independently of cached bars. It must cover the full data
horizon required by the scan, up to maximum duration, or a verified shorter listing
history. A truncated provider response is not evidence of a shorter listing history.
The scanner does not trust the last cache timestamp to determine eligibility.

TimeSeries timestamps at this boundary are canonical candle-open instants, including
daily sessions. Mapping provider daily dates to session identities belongs to prompt 6.
The last bar must equal the explicit expected final candle. Cutoff, interval and
unfinished settings must agree. Prices/OHLC/volume must be valid, finite, positive
where applicable, chronologically ordered, and unique. The required schedule must
be ordered, non-overlapping and identity-consistent. Within the required tail, bar
timestamps must equal the schedule exactly. Known missing bars are never bridged.
An invalid required tail rejects the stock rather than selecting a shorter gap-free
suffix and concealing missing coverage. Extra history before that tail is not scored.

Available valid observations bound possible N. Missing observations within the
declared horizon reject the input; a verified shorter listing history can still
support forming hypotheses with a longer hypothetical complete duration. If no
hypothesis meets the eight-observation/completion rules, the stock is skipped as
insufficient. At most 10,000 bars/required identities and 50,000 unique stocks are
accepted at this computation boundary.

## Full and forming matching

For every allowed integer complete duration D, enumerate observed N >= 8 with
N <= min(D, available observations). Completion is exactly Fraction(N-1,D-1).
Minimum completion is interpreted from its decimal setting with Fraction(str(value)).
There is no completion grid or hidden duration reduction. At defaults and 120
available observations, each stock has 3,560 applicable hypotheses before movement
and degeneracy rejection.

Every hypothesis uses the last N required observations and the same expected final
candle. Full means N=D; forming means N<D. For D=21, N=11 is exactly 50%, while
N=10 is rejected at the default. For D=20, N=11 is the first accepted N (52.63%).

The All/Full/Forming setting selects hypotheses before scoring and per-stock
selection. Actual close movement is independently filtered with Decimal arithmetic:
100 * (max_close/min_close - 1), inclusive of the configured threshold. A V can
qualify despite zero net change. Flat candidates and reference prefixes never score,
including when minimum movement and similarity are both zero.

## current_shape_v1

1. Clip the piecewise-linear canonical reference to [0,p], inserting its endpoint.
   For [(0,2),(.4,6),(1,0)] clipped at .5, the result is [(0,2),(.4,6),(.5,5)].
2. Subtract the clipped prefix's first y and divide by its own range, then sample
   that prefix at 64 equally spaced progress positions. No unmatched extrema or
   continuation samples enter this process.
3. Convert each actual candidate suffix to log-relative closes once, then perform
   the same range normalization/resampling. `log1p` of Decimal-relative differences
   retains small movements; Decimal logarithms handle extreme ratios when needed.
4. Mean-center and normalize each sampled path to unit RMS. A zero range, or RMS
   <= 1e-12 AFTER range normalization, is degenerate. For example, a needle missed
   by all 64 samples is not meaningful sampled evidence.
5. Apply the legacy component mathematics: mapped cosine shape/direction and
   nonnegative fitted-scale residual error, followed by exp(-2*RMS error).
6. Overall = (45*shape + 30*direction + 20*error)/95. Amplitude is null.

The direct path is mathematically equivalent to the specified positive pseudo-price
transform followed by log normalization, but avoids exponentiation and a second log.
Direction tolerances (1e-8) apply only to unit-RMS paths/slopes. Simply subtracting
the legacy amplitude contribution would retain an amplitude-sensitive degeneracy
threshold, so the existing legacy prepare method is intentionally not reused.
Component agreement is checked against the legacy scorer using normalized
pseudo-prices, and existing scorer regression tests remain intact.

Reference translation and positive vertical scaling preserve scores within floating
point precision and valid coordinate bounds. Extremely small differences lost during
coordinate creation cannot be recovered. The score is an uncalibrated shape metric,
not a probability, forecast, or profit measurement. No legacy score bands are used.

Similarity thresholds use unrounded values. A score just below a threshold does
not pass by rounding up. Results retain raw values; formatting belongs to the UI.
Tie-breaking alone uses six-decimal scores, then higher exact completion, longer
observed history, and smaller complete duration. Global rounded-score ties use
symbol ascending. Each stock appears once, with top_n applied after global ranking.

## Bounded execution

The server-owned scanner accepts at most 1,000,000 actual pair scores and batches
of 1..256 hypotheses. Each batch also bounds prefix preparation/enumeration. Prefix
preparations are cached by exact completion within the scan; candidate preparations
are cached by suffix length within a stock. Repeated pairs still count as actual
score work. Movement/degeneracy rejections do not increment hypotheses_evaluated.

ScanControl separates frozen market cutoff from an injected monotonic deadline and
cancellation callback. Checks occur before stocks, between bounded batches, and
after scoring before publishing a stock. Candidate preparation is bounded by at
most 240 suffixes. A stock interrupted after partial evaluation contributes no
ranked result, even if a provisional alignment had a high score. Previously finished
stocks remain usable. An exact hypothesis budget that finishes a stock permits
publishing that stock; subsequent unfinished work remains excluded.

CurrentScanEvaluation reuses foundation matches, Coverage and ExecutionStatus, plus
per-stock terminal exclusion reasons. Terminal counts partition the supplied frozen
universe. Eligible overlaps scanned and incomplete eligible stocks. Discarded
partial stocks count in not_evaluated and evaluation_incomplete. A stock not started
after cancellation is not_evaluated but not evaluation_incomplete. No provider calls
occur, and provider_requests is zero. Prompt 6 must represent omitted universe
members in total coverage rather than implying the supplied ready subset is the
entire universe.

## Focused performance measurements

Command from backend: `python -B -m scripts.benchmark_current_pattern --stocks N`.
The script uses explicit fictional sessions, 120 positive synthetic daily closes
per stock, default 20..120 duration, 50% completion, 5% movement, score threshold 80,
and All matching. Timing includes validation, preparation, enumeration and selection,
but excludes synthetic data creation, network, storage, calendars and UI.

| Stocks | Actual scored hypotheses | Local elapsed seconds |
| --- | ---: | ---: |
| 1 | 3,560 | 0.213 |
| 10 | 35,596 | 0.855 |
| 50 | 177,956 | 3.801 |
| 100 | 355,934 | 6.305 |

Not all enumerated hypotheses pass movement filtering. These are single local
Windows/Python 3.12.14 observations, not latency guarantees or formal statistics.
The first runs overlapped other verification processes; the 100-stock measurement
ran separately. An eight-second budget may yield partial coverage, especially on
deployment hardware or a larger universe. It must be enforced, measured and clearly
reported, not assumed feasible for the full universe.

## Prompt 6 integration

- Establish real exchange calendars, timestamp conversion, timing/finalization and
  unfinished snapshot capabilities; unknown remains unavailable.
- Retrieve/cache complete required observation schedules and prices without using
  returned bar timestamps as the calendar or treating truncation as coverage.
- Add provider-attempt/credit budgets, controlled hydration, cancellation propagation
  and persisted freshness metadata where justified.
- Connect this computation result to request revisions and the frozen universe's
  full coverage accounting; register public routes only in the authorized stage.
- Benchmark on deployment hardware. Keep the known async runtime blocker explicit
  until fixed; no provider logic workaround or repeated probe is part of this stage.
