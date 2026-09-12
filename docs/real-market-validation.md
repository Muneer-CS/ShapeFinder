# Real-market validation

Phase 10 uses the normal Twelve Data → cache → scanner path to assess Similarity Engine V1 on a small, deliberately non-statistical sample. The scoring formula remains 45% shape, 30% direction, 20% fitted-path error, and 5% amplitude. Scores are engineered comparisons, not probabilities.

## Security and quota policy

Place `TWELVE_DATA_API_KEY` only in the ignored `backend/.env` file. Never pass it as a command argument, put it in frontend configuration, or save it in reports. The validation CLI reads the existing backend settings and SQLite cache. It requests only missing coverage, so rerunning an identical historical case should be a cache hit. Start with two or three candidates, expand to the ten-symbol set only after authentication and account capabilities are confirmed, and never use this tool to hydrate a broad universe.

Generated JSON belongs under the ignored `backend/validation-output/` directory. Runtime SQLite files and `.env` files are also ignored.

## Validation command

Run from `backend` with the virtual environment active:

```bash
python scripts/validate_real_market.py \
  --reference AAPL \
  --reference-start 2024-04-01T00:00:00Z \
  --reference-end 2024-04-12T23:59:59Z \
  --interval 1day \
  --search-start 2022-01-01T00:00:00Z \
  --search-end 2024-03-31T23:59:59Z \
  --candidates AAPL,MSFT,NVDA \
  --top-n 10 \
  --json validation-output/aapl-short.json
```

The report includes exact reference and match periods, all five scores, scan statistics, data-load/scan/total timings, and per-series diagnostics. Use UTC day boundaries for daily experiments, matching the frontend and allowing identical cache coverage to be reused. Diagnostics flag duplicate or unordered timestamps, invalid prices or volumes, malformed OHLC relationships, zero volume, timezone inconsistencies, and unusually large gaps. Large gaps are informational because weekends, holidays, halts, and overnight closures are legitimate.

## Reproducible experiment matrix

The controlled full candidate set is `AAPL,MSFT,NVDA,AMD,AMZN,META,GOOGL,TSLA,JPM,XOM`. Initial authentication should use only `AAPL,MSFT,NVDA`.

| Case | Reference | Fixed reference period | Search period | Purpose |
| --- | --- | --- | --- | --- |
| Short daily | AAPL | 2024-04-01 through 2024-04-12 | 2022-01-01 through 2024-03-31 | 5–10 trading-day behavior |
| Medium daily | MSFT | 2023-10-02 through 2023-11-10 | 2020-01-01 through 2023-10-01 | roughly 30 trading days |
| Long daily | NVDA | 2023-01-03 through 2023-06-16 | 2018-01-01 through 2022-12-31 | roughly 115 trading days |
| Optional intraday | AAPL, 5min | 2024-02-01 14:30–21:00 UTC | 2024-01-22 through 2024-01-31 | timing/noise sensitivity if the tier permits |

The same fixed periods must be reused so future runs are comparable. A separate recent AAPL daily request should cover only a modest recent range and use qualitative freshness assertions rather than hard-coded current prices.

## Review protocol

For each case, inspect the highest, middle, and lowest returned matches in the existing side-by-side and rebased-overlay UI. Record whether turning points align, whether gaps or spikes dominate, whether a generic trend is over-rewarded, and whether direction and 5% amplitude scores are plausible. Search the reference symbol against its own earlier history and confirm that the reference window and heavily overlapping windows are absent while older legitimate patterns remain. Check that selected previews use the exact returned start/end timestamps and that retained windows are visually distinct after overlap suppression.

Do not create descriptive score bands from this small sample. Do not change weights in response to an isolated surprising match. A proposed formula change requires a recorded reference/candidate pair, component scores, visual rejection rationale, suspected metric, expected side effects, and a minimal regression fixture.

## Live execution status

The Phase 10 live gate completed with an authenticated Twelve Data configuration. No key, raw response, runtime database, report, or screenshot is committed.

Five liquid symbols were used for daily cases: AAPL, MSFT, NVDA, JPM, and XOM. The short AAPL case contained 9 bars, the medium MSFT case 30 bars, and the long NVDA case 115 bars. A supported 5-minute AAPL case contained 78 bars and searched AAPL, MSFT, and NVDA. All loaded series reported zero data-quality errors.

Representative results:

| Case | Top match | Overall | Shape | Direction | Error | Amplitude |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Short AAPL | JPM, 2023-04-03–2023-04-14 | 80.92 | 94.31 | 95.10 | 39.59 | 40.67 |
| Medium MSFT | MSFT, 2021-03-03–2021-04-14 | 80.47 | 98.03 | 67.30 | 57.39 | 93.75 |
| Long NVDA | NVDA, 2020-03-25–2020-09-04 | 81.19 | 98.33 | 69.33 | 59.93 | 83.06 |
| AAPL 5-minute | MSFT, 2024-01-29 15:45–2024-01-30 15:40 UTC | 66.27 | 89.16 | 57.88 | 28.84 | 60.31 |

The existing UI was used to load real references, run custom and cached NASDAQ searches, expand component scores, load exact-period previews, select matches, and inspect actual-price charts plus rebased overlays. The short same-stock overlay and medium/long overlays were visually convincing at their reported overall scores. Smooth upward trends often produced shape components near 98, but direction and fitted-path error reduced the overall score to roughly 80; no suspicious near-100 overall result appeared. This is evidence that the combined metrics moderate a generic-trend false positive, not enough evidence to invent score bands.

No formula change was made. The 5-minute experiment showed a limitation: bar-count windows may cross an overnight market-session boundary. Its best such match scored only 66.27, but session-aware intraday windows deserve separate design rather than an unreviewed Phase 10 semantic change. The small sample also cannot establish behavior across regimes, instruments, corporate actions, or provider tiers.

A same-stock AAPL scan spanning the reference period excluded the exact and heavily overlapping reference windows while retaining older/non-overlapping patterns; its best retained match was 2024-05-30–2024-06-11 at 79.07. Within each symbol, retained real matches were separated by the existing greater-than-50% overlap rule and appeared temporally distinct. Cross-symbol periods may overlap by design.

An immediate repeat of the first historical request reduced data loading from 0.265 seconds to 0.020 seconds with identical results. A recent AAPL daily request returned 8 bars through 2026-09-11. Its first request fetched the missing range once; the immediate second request fetched only the documented trailing 2026-09-09–2026-09-12 slice, leaving the cached bar count stable. This confirms both historical reuse and recent-edge refresh behavior without asserting a brittle current price.

One live universe request returned 16,412 provider rows. Exactly one row had a blank name; after the isolated-row tolerance fix, 16,409 normalized unique active U.S. common-stock symbols were persisted (3,681 NASDAQ and 2,078 NYSE). The immediate second list used the 24-hour cache, so the pair consumed one provider call. A cached NASDAQ UI search truthfully scanned the 3 locally ready symbols, skipped 3,678 without complete coverage, and evaluated 1,661 windows.

Cached five-symbol performance was 0.065 seconds loading plus 0.154 seconds scanning for the 4,565-window medium case, and 0.080 seconds loading plus 0.322 seconds scanning for the 5,715-window long case. These measurements are local observations, not timing guarantees.
