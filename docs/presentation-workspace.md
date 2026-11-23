# Stage 9: presentation and frozen comparisons

The visual direction uses deep navy and warm off-white themes, teal accents,
system sans-serif controls and Georgia serif headings. The supplied attachment
contained the written direction only; there was no selected mockup image available
for visual matching. No illustrative reference values became application defaults.

## Components and state

- `App.tsx`, `workspace/ThemeControl.tsx` and `workspace/About.tsx` provide the shell,
  hash-based Scanner/About navigation and exact project/profile destinations.
  Scanner remains mounted while About is shown, preserving input drafts, local
  images, acceptance, settings, results and comparison selection. Navigation does
  not request prices or scans. The in-page skip link preserves the active page.
- `workspace/workspace.css` defines shared light/dark tokens for controls, charts,
  overlays, tracing, focus, badges and menus. The initial theme follows the device.
  Explicit choices persist in `shapefinder.theme`; Use device theme removes the
  override. Storage failure retains a session choice without breaking the app.
  The tiny same-origin `public/theme-init.js` runs before styles/React to avoid a
  wrong-theme first paint. No remote fonts or images are required.
- `PatternWorkspace.tsx` places editor/settings beside results on desktop, with
  Run/Cancel and availability above both columns. Numeric tools, guides, revisions,
  raw counters and timestamp diagnostics are labelled disclosures. All four input
  workflows still use the same editor. Source forms preserve their drafts and
  collapse when appropriate; no method switch fabricates acceptance.
- `ScanResults.tsx` retains server order and filtering, selects one unique stock,
  distinguishes no-match/partial/unavailable/previous states and exposes optional
  diagnostics. `ComparisonChart.tsx` uses SVG miniatures and one selected detail
  view; it never loads prices. Actual observed closes have a separate linear-price
  view and an exact timestamp/value table. The history contract does not supply
  currency, so the view explicitly labels quoted units rather than assuming USD.

## Small backward-compatible response extension

`core/pattern_scan.py` adds optional `CurrentPatternMatch.comparison`:

```json
{
  "observations": [{"timestamp": "2025-01-01T14:30:00Z", "close": "100.125"}],
  "log_relative_closes": [0.0]
}
```

This is a shape excerpt only: real snapshots contain 8 to 240 observations/logs,
with equal lengths, ordered aware timestamps, a zero log origin, and finite,
positive decimal price strings limited to 128 characters. The model requires
exact match length/start/end identity. Only global top_n results receive snapshots,
at most 100 results and 240 observations each. Unusually long decimal encodings
omit the optional comparison without changing ranking, scoring or coverage.

The scanner reads the exact matched suffix from its already-validated immutable
input history after ranking. It recomputes the same accurate log-relative transform
for those selected closes only. No provider/cache/database request, new scheduling,
schema migration, score change or additional alignment hypothesis is introduced.
The API route already serializes domain matches, so no new endpoint is needed.
The integration assertion checks that the response contains the exact suffix and
that provider attempts remain unchanged. Old responses/fixtures without comparison
samples still show their ranked measurements, explicitly without an overlay.

The frontend clones the submitted canonical pattern, then stores that snapshot
with the validated response/hash. Later editor/settings changes label results
previous and never change their reference or observed prices. Request identity,
abort and revision guards still protect late replies.

## Alignment definition

`pattern/comparison.ts` reproduces `current_shape_v1` display preparation:

1. Compute progress p = (observed-1)/(complete-1), clip reference at p and insert
   its interpolated endpoint.
2. Range-normalize using only that prefix, resample 64 points, mean-center and
   normalize to unit RMS. Prepare the server's accurate log-close path similarly.
3. Apply the scorer's nonnegative least-squares stock scale. Never mirror a path.
4. Put the stock and matched reference on x=0..p. Transform the remaining reference
   using that same prefix transformation and show it on p..1 with a distinct line.

The observed line stops at p. The reference continuation is explicitly not a
prediction. Stage 10 expands the display axis to show the complete reference.
The prefix transformation and stock fit still use only matched data.
Nonfinite continuation coordinates do not become invalid SVG paths. Price view
uses stable expm1-based linear price ratios from the server log path, preserving
tiny ranges and avoiding exponent overflow; exact quoted values remain available.
Its separate time axis uses UTC observation timestamps, never hypothetical future
dates. Python-generated non-linear preparation/fit vectors verify the frontend math.

## Verification boundary and stage 10

Frontend gate: `npm run check` from `frontend` (lint, formatting, offline tests,
TypeScript, production build) passed with 113 tests. Focused tests cover device/saved/unavailable-storage
themes and prepaint bootstrap, navigation/state/links, snapshot isolation, partial
endpoints, interpolated prefixes, nonnegative fit and accurate price positions.
Existing input, approval, cancellation and outcome regressions remain in the gate.

Available backend gate: Ruff check/format and mypy plus synchronous computation and
contract tests passed, including 94 tests in `test_current_pattern_scan.py` and
`test_pattern_scan_contracts.py`. The known async execution limitation was not re-probed. The earlier
326-test/94.48% full gate is user-verified historical evidence; the new comparison
integration assertion and the new full coverage gate require normal PowerShell.

From `backend`, targeted integration followed by the full gate:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -o addopts= tests/test_current_pattern_scan.py tests/test_pattern_scan_contracts.py tests/test_manual_pattern_scan.py -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning
.\.venv\Scripts\python.exe -B -m ruff check src tests scripts
.\.venv\Scripts\python.exe -B -m ruff format --check src tests scripts
.\.venv\Scripts\python.exe -B -m mypy src/shape_finder
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -W error::RuntimeWarning -W error::pytest.PytestUnraisableExceptionWarning
```

Browser verification uses a local mock HTTP server and synthetic observations
evaluated by the actual offline scanner. Final screenshots show desktop dark and
light at 1440 pixels, a narrow light layout at 390 pixels, and About. Browser
checks covered drawing in an earlier stage 9 build, accepted text previews,
partial coverage and full/forming results, comparison selection, the observed
price view, About and Back with preserved selection, and loading mock stock
history. The local preview stopped responding before the final-build browser
checks for completing stock import, image tracing, unavailable/error/no-match
scenarios, and narrow dark mode. Automated input and outcome regressions passed.
The fixture and screenshots live outside the repository and use synthetic data.

## Stage 10 local verification

The final frontend gate passed with 113 tests, lint, formatting, TypeScript and
the production build. Backend Ruff and mypy passed. The full backend gate passed
with 329 tests and 94.55% coverage, including the public comparison integration
assertion. The successful backend run kept the configured 90% coverage floor and
treated coroutine and unraisable warnings as errors. It used a disposable pytest
temp directory and blocked non-loopback socket connections and DNS lookups.

Browser checks on the production frontend build used loopback-only fixtures.
They covered stock-section import, local image tracing and acceptance, unavailable
capabilities, a catalogue error, completed no-match results, and partial results
with a scoring deadline. Both saved themes survived reload. About and Back
preserved an accepted description, results and the selected stock. At 390 pixels
the full dashed reference fit the chart, the observed line ended at 60%, and the
theme control had a visible keyboard focus outline. The theme menu also fit at
320 pixels without horizontal overflow.

These sample timings include offline scoring and JSON serialization, excluding
fixture generation, HTTP transport, cache hydration and browser rendering.

| Fixture | Evaluated | Results | Observations per result | JSON bytes | Elapsed |
| --- | ---: | ---: | ---: | ---: | ---: |
| Representative | 12 of 12 | 12 | 60 | 72,440 | 0.409 s |
| Maximum result and observation counts | 101 of 101 | 100 | 240 | 2,134,295 | 7.930 s |
| Same workload with default 8-second scoring budget | 98 of 101 | 98 | 240 | 2,091,654 | 8.171 s |
| Injected deadline during the second stock | 1 of 4 | 1 | 240 | 22,384 | 0.143 s |

Every snapshot matched its scored suffix exactly, including timestamps, decimal
strings and log-relative closes. Provider attempts were zero. The injected
deadline left three stocks unevaluated, including one interrupted stock whose
provisional match was discarded. The wall-clock result varies with load. The
8-second check is cooperative and allows in-progress work and serialization to
finish after the stop. The byte counts are uncompressed for these fixture prices.
The measurement driver is `../shapefinder-stage9-checks/verify-scan-bounds.py`
relative to the checkout, with `backend` as the working directory and on
`PYTHONPATH`.

## Daily provider validation before enabling real scans

The local configuration has a Twelve Data key and the official endpoint, but
has no timing or account policy. The cached catalogue contains an active NASDAQ
Common Stock listing for AAPL, last refreshed September 12, 2026 UTC. The local
database does not yet have the current-candle cache table. Take the existing
pre-migration backup when running the upgraded backend against that database, or
use a disposable copy for the pilot.

Twelve Data documents Basic access to US listed-equity daily history after
midnight Eastern on the next trading day. Daily labels are exchange-local
session dates. The guide does not supply an account-specific finalization SLA
or a fixed elapsed-time guarantee across weekends, holidays and early closes.
The app currently represents timing as seconds after each session close. A fixed
eight-hour policy would not represent the documented next-trading-day rule.
Keep the daily capability unavailable until there is a defensible policy for the
intended operating window. Keep intraday and unfinished candles unavailable.

- Confirm the account's plan and quotas in the provider dashboard, without an
  API call. For fresh membership, budget one `/stocks` request filtered to US
  Common Stock. Reuse a suitable cached listing for a deliberately limited pilot.
- With separate approval, use the actual account to query one liquid NASDAQ or
  NYSE common stock using `/time_series`, `interval=1day`, `order=ASC`,
  `adjust=splits`, `prepost=false` and start/end dates for 20 consecutive
  exchange sessions. Use a candidate checkpoint after the documented publication
  window for the last session. Require expected listing identity and timezone,
  one valid OHLCV row per requested session, and the expected last session date.
  Repeat the same range at a later checkpoint and compare the last bar. Two
  requests cost two documented credits. This establishes observed behaviour for
  that window. Obtain a timing commitment or validate relevant session boundaries
  before generalizing it. Stop on denied entitlement, 429, a missing latest row,
  ambiguous listing, or a revision beyond the proposed finalization cutoff.
- After a policy is justified, use one custom symbol with `20..20` candles,
  completed mode, and one allowed daily attempt. A cold scan should make at most
  one history request and evaluate one symbol if the range is complete. A repeat
  with the same expected session should use the validated cache with zero
  attempts. Treat `configured_but_unverified` as operator policy, and check that
  the result or no-match coverage identifies the correct completed session.

Sources: [US equities](https://support.twelvedata.com/en/articles/9935903-us-equities-market-data),
[time series](https://twelvedata.com/docs/market-data/time-series),
[stocks catalogue](https://twelvedata.com/docs/asset-catalogs/stocks-list), and
[credit rules](https://support.twelvedata.com/en/articles/5615854-credits).
