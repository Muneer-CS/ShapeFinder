# Architecture boundaries

## Dependency direction

Dependencies point inward: API and infrastructure depend on application/core contracts, never the reverse. The frontend knows only the public HTTP contract.

## Frontend flow

The React application holds separate reference and search configurations. The reference range is converted to timezone-aware API boundaries and sent through the typed `marketData` client. After that succeeds, the search configuration is sent through a separately typed similarity-search client using the exact Phase 6 HTTP contract.

```text
reference form → typed HTTP client → FastAPI route → MarketDataService
                                                      ├── SQLite cache
                                                      └── Twelve Data (when needed)
API bars → exact close values → responsive Recharts reference chart
search form → typed similarity client → ranked result cards + statistics
selected match → lazy exact-period market-data request → comparison charts
```

Daily inputs use calendar-day boundaries; intraday inputs use the browser's local date-time control and are serialized as UTC ISO 8601 values. Public API error codes are mapped to safe, actionable UI messages without exposing provider payloads or credentials.

### Phase 7 UI state and previews

The loaded reference records the exact submitted query separately from editable form state. Editing any reference field aborts an in-flight reference request and invalidates the loaded chart, search response, selection, and previews, so stale results cannot be represented as belonging to a new reference. Editing search dates, candidate symbols, result count, or threshold clears only search-derived state and preserves the loaded reference.

Candidate symbols are normalized to uppercase, trimmed, de-duplicated, and capped at 10 in the browser; the backend independently enforces the same limit. Search submission is disabled without a loaded reference and while a request is active. Results retain backend ranking and present the score as an engineered similarity measure—not a probability or prediction.

Result charts are deliberately lazy. A preview or selection calls the existing market-data endpoint with that match's exact symbol, start, end, and interval. Failures are local to that preview and never remove the match. The optional comparison overlay linearly aligns the returned observations by relative position and rebases each series to 100 for display only. It does not reproduce or alter Similarity Engine V1 scoring.

## Backend packages

- `core`: provider-neutral domain models and protocols for OHLCV market data, similarity analysis, and persistence.
- `application`: `MarketDataService` computes missing coverage, refreshes the recent edge, chunks requests, and coordinates atomic persistence.
- `infrastructure`: `TwelveDataProvider` owns provider HTTP behavior; `SQLiteMarketDataRepository` owns relational storage and migrations.
- `api`: transport concerns such as FastAPI routes and response schemas.

## Intended extension points

`MarketDataProvider.get_historical_bars` retrieves normalized external data. `MarketDataRepository` stores and queries normalized bars plus synchronization coverage. `MarketDataService` depends only on these protocols. `SimilarityEngine` isolates numerical analysis, while `SimilarityResultRepository` remains the boundary for future result storage.

## Similarity Engine V1

`ChartSimilarityEngine` is a synchronous, side-effect-free application component implementing the core `SimilarityEngine` protocol. It compares two explicitly supplied sequences of close prices and returns an immutable `SimilarityScore`. It has no API, database, provider, or UI dependency.

### Normalization and alignment

Each positive close is transformed to log-relative space, `log(priceᵢ) − log(price₀)`. This removes absolute price level and represents multiplicative market movement consistently. Each resulting path is linearly interpolated to 64 evenly spaced points over its normalized start-to-end timeline. This permits slightly different bar counts and missing intermediate observations without assigning values beyond the two observed endpoints. Dynamic time warping is deliberately excluded: moving a peak in time should reduce similarity rather than be hidden by alignment.

The aligned path is centered and divided by its root-mean-square amplitude for the shape-oriented metrics. This makes equal shapes with moderate return-amplitude differences compare strongly. The original log-path RMS amplitudes are retained for a small magnitude component.

### Metrics and exact score

All component scores are clamped to `[0, 100]` and the final values are rounded to six decimal places.

- **Shape score:** `50 × (1 + cosine(centered standardized paths))`. For centered vectors this is Pearson correlation mapped from `[-1, 1]` to `[0, 100]`.
- **Direction score:** `50 × (1 + cosine(first differences of standardized paths))`. This penalizes disagreement in rise/fall sequence, slope, turning points, and their timing.
- **Error score:** the candidate standardized path is fitted to the reference by a non-negative least-squares scale; `100 × exp(−2 × RMS residual)`. The non-negative constraint prevents an inverted path from becoming a good fit merely by flipping its sign.
- **Amplitude score:** `100 × min(log-path RMS amplitudes) / max(log-path RMS amplitudes)`.
- **Overall score:** `0.45 × shape + 0.30 × direction + 0.20 × error + 0.05 × amplitude`.

Amplitude therefore affects only 5% of the overall score. A half-amplitude copy of the same path can remain a very strong match, while a radically different amplitude still has a bounded effect. The larger weights reward the path itself and the ordered movement sequence.

### Flat series, validation, and numerical stability

A path with centered log-path RMS at or below `1e-8` is treated as flat. Flat versus flat returns 100 even when the absolute prices differ; flat versus non-flat returns 0. This avoids undefined correlation. Inputs require at least two finite, strictly positive closes. Booleans, zero, negative values, NaN, and infinity are rejected with `InvalidSimilarityInputError`.

Ordinary values use fast double-precision logarithms. Values outside the finite floating-point range fall back to fixed 50-digit decimal logarithms, keeping extreme positive decimal scales stable and the result independent of ambient decimal precision.

### Complexity, assumptions, and limitations

For input lengths `n` and `m`, time complexity is `O(n + m + 64)` and working memory is `O(n + m + 64)`. A local timing sanity check averaged about 1.6 ms per comparison for two 500-point series on the development machine; this is indicative, not a performance guarantee.

V1 assumes observations are ordered and reasonably cover comparable start-to-end periods. Because its input contains closes rather than timestamps, interpolation treats observations as evenly spaced along each period. It uses no volume, candlestick features, indicators, fundamentals, sectors, news, or machine learning. The engine itself performs no retrieval, scanning, or prediction. The engineered weights and practical score thresholds are deterministic but not statistically validated; later real-market evaluation may refine them.

## Historical similarity scanner

`SimilaritySearchService` is the Phase 6 orchestration boundary. It normalizes and de-duplicates symbols, retrieves the reference once, then retrieves each candidate range once and sequentially through `MarketDataService`. This preserves SQLite caching and conservative provider usage. Any reference or candidate retrieval failure fails the whole request; Phase 6 does not return silent partial results.

`HistoricalSimilarityScanner` performs no I/O. It prepares the reference's fixed engine representation once and reuses it for every window; `compare_prepared` is tested to produce exactly the same result as the ordinary Phase 5 comparison path. Its candidate window length equals the number of valid reference bars, not the reference's calendar duration. It keeps chronological, timezone-aware bars whose close is finite and positive and whose timestamp falls inside the inclusive search range. Missing or invalid observations are not fabricated; therefore equal-bar windows can span different calendar durations.

### Windowing and selection

- The default stride is one valid bar. For `L` reference bars and `N` candidate bars, offsets `0` through `N − L` are evaluated, including the final possible window.
- Reference and candidate intervals must match exactly.
- Ranking is deterministic: overall score descending, candidate start ascending, symbol ascending, then candidate end ascending.
- `minimum_similarity`, when present, removes scores below its inclusive 0–100 threshold before selection. `top_n` returns at most 1–100 matches.
- After ranking, a lower-ranked window is suppressed when it shares **more than 50%** of its bar timestamps with an already selected window for the same symbol. Equal-score duplicates therefore collapse to one representative event; separate events and matches from different symbols remain eligible.
- For the reference symbol itself, a candidate is excluded before scoring when it shares more than 50% of its timestamps with the loaded reference. This removes the exact reference and nearby trivial shifts while preserving distinct history elsewhere in the same stock.

The overlap and self-overlap thresholds, plus stride, are constructor settings for controlled future tuning. Defaults are fixed for the public Phase 6 service.

### API contract and limits

`POST /api/v1/similarity/search` accepts structured `reference` and `search` objects. The backend—not the browser—loads reference closes. The request permits at most 10 supplied candidate symbols, normalizes whitespace/case, de-duplicates repeated symbols, and validates timestamp order/timezones, interval, `top_n`, and score threshold. The response contains a reference summary, the enforced search range, provider-neutral match periods and score components, plus counts for requested/scanned symbols, evaluated/passing windows, and returned matches.

For `S` symbols with `Nₛ` candidate bars and reference length `L`, scanning performs `Σ max(0, Nₛ − L + 1)` engine comparisons at stride one. Data retrieval is once per series; no database query occurs per window. Phase 6 intentionally sorts the in-memory scored windows to keep selection simple and correct. A development-machine sanity run with five 1,000-bar candidates and a 30-bar reference evaluated 4,855 windows in 0.936 seconds, about 5,189 comparisons/second. This is indicative rather than a performance guarantee and is suitable for a small explicit list, not full-market scale.

Phase 6 does not discover a stock universe, scan thousands of symbols, schedule jobs, distribute work, persist scan results, or render results in the frontend. It uses Similarity Engine V1 unchanged, and its score remains an engineered—not statistically validated—measure.

## SQLite schema and migrations

`market_bars` uses `(symbol, interval, timestamp_utc)` as its `WITHOUT ROWID` primary key. It stores OHLCV decimals as exact text, plus timezone, source, and fetch time. This primary key is also the required range-query index; an additional index would be redundant.

`market_data_coverage` records successfully synchronized inclusive ranges even when weekends or other non-trading periods contain no bars. `schema_migrations` records each applied migration. Migrations are append-only, ordered, and run transactionally during application startup.

Repository methods open bounded SQLite connections and run blocking database work off the async event loop. Upserts and coverage writes share a transaction. All provider chunks are fetched before that transaction begins, so a failed provider request cannot partially overwrite the cache.

## Cache policy

Coverage intervals determine missing ranges. Completed historical ranges are immutable cache hits. A request whose end falls within three interval durations of the current time refreshes that trailing three-period edge, allowing active and recently corrected candles to be updated. No scheduler or exchange calendar is assumed.

Provider requests are divided into at most 4,500 theoretical interval points. Adjacent chunks are inclusive and the next chunk begins one exact interval after the previous end, preventing both gaps and duplicate boundaries.

## Timestamp policy

API callers must send timezone-aware boundaries. For intraday intervals, the adapter converts boundaries to UTC, requests `timezone=UTC`, and attaches UTC to returned timestamps. For daily data, Twelve Data ignores the timezone parameter; ShapeFinder sends calendar dates and attaches the IANA `exchange_timezone` returned in provider metadata. Missing or unknown provider timezone metadata is rejected rather than guessed.

The endpoint returns bars in chronological order. Non-trading periods naturally contain no bars, and a completely empty result becomes a typed `NO_DATA` response.

## Failure boundary

Twelve Data-specific error bodies are translated to stable internal exceptions. The API maps those exceptions to consistent public codes without forwarding raw payloads, stack traces, request URLs, or credentials. A shared async HTTP client is owned by the FastAPI lifespan.

## Phase boundary

Phase 7 adds the user-facing workflow for the existing on-demand scanner, including ranked results, lazy previews, selection, and comparison. Full-market discovery/scanning, result persistence, prediction, background jobs, deployment, and authentication remain out of scope.
