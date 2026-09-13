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

## Stock universe layer

`UniverseProvider` returns normalized `SymbolMetadata`; `UniverseRepository` persists catalog snapshots and answers scan-readiness queries; `UniverseService` owns normalization, filtering, TTL, stale-cache fallback, and named-universe resolution. The Twelve Data adapter is the only layer that knows the official `/stocks` payload. FastAPI routes and React use stable universe IDs rather than provider fields.

```text
Twelve Data /stocks → TwelveDataProvider → UniverseService → UniverseRepository
                                               │                 └── SQLite snapshot
                                               └── named symbols
                                                      ↓
request → SimilaritySearchService → batched readiness query → cached TimeSeries
                                                      ↓
                                      HistoricalSimilarityScanner
```

The initial named universes are `us_equities`, `nasdaq`, and `nyse`; `custom` preserves the Phase 7 symbol-list contract. Membership is limited to active U.S. `Common Stock` records. Exact exchange equality defines NASDAQ and NYSE subsets. Index membership such as S&P 500 is deferred until an authoritative maintainable source is available.

Migration 2 adds normalized `universe_symbols` and `universe_refresh` tables. A refresh is a single transaction that replaces the last successful snapshot; failed refreshes cannot partially change membership. The default 24-hour TTL is configurable. Fresh cache avoids a provider call. Stale cache is served with `universe_stale=true` when provider refresh fails.

Broad-search readiness performs batched SQL queries over `market_data_coverage` and grouped bar counts, requiring complete range coverage, exact interval, and at least the reference bar count. `UniverseHydrationService` resolves the selected universe, measures ready coverage, and sends only a bounded deterministic slice of missing symbols through the existing cache-aware `MarketDataService`. It fetches the requested search range and interval, recomputes readiness, and the scanner loads every currently eligible series.

Daily hydration defaults to 5 symbols per request; intraday defaults to 2. A separate 15-second hydration deadline preserves time for scanning inside the endpoint's outer deadline. Rate limits and provider outages stop the batch without rolling back completed cache writes, after which any ready symbols are still scanned. Invalid/no-data responses affect only their symbol. Structured statistics report readiness before and after, attempts, successes, failures, provider-stop reasons, and truthful scanned/skipped coverage.

Ticker ordering is stable. A process-local cursor keyed by universe, range, interval, and reference length rotates beyond the last attempted ticker, while scan-ready symbols are always skipped. Successful bars and coverage are durable in the existing SQLite database and therefore survive restart; the failure cursor intentionally is not persisted, so an earlier failed ticker may be retried after restart. Per-context async locks prevent concurrent identical searches from duplicating hydration work in one process.

The request guard rejects more than 50,000 resolved symbols or an estimate above 2,000,000 windows. The larger catalog ceiling accommodates the provider's full U.S. stock catalog; the readiness filter and window guard still bound actual scanning. Scanner scoring remains single-process and deterministic. To reduce memory without changing ranking, each symbol is independently sorted, overlap-suppressed, and trimmed to `top_n` before global sorting. Since overlap suppression never crosses symbols, no possible global top-N result is discarded. Distributed work and background queues remain out of scope.

### Phase 9 scanner optimization

A `cProfile` baseline showed that scoring—not window enumeration, ranking, or overlap suppression—dominated broad scans. Within scoring, repeated preparation of each candidate window, log-relative normalization, 64-point interpolation, and scalar dot products accounted for nearly all runtime.

`BatchSimilarityEngine` is an optional core capability, so alternative engines can keep using the original scalar protocol. `ChartSimilarityEngine.compare_many` applies the exact V1 weights and policies to equal-length windows with NumPy: it normalizes the whole batch, reuses a cached interpolation plan for each input length, and vectorizes shape, direction, fitted-error, and amplitude components. The scanner converts one symbol's closes to a contiguous float array once, obtains rolling windows through `sliding_window_view`, and materializes at most 4,096 indexed windows per batch. This bounds temporary memory while retaining deterministic symbol order, timestamps, filtering, overlap suppression, tie-breaking, and final-window inclusion. Non-finite, non-positive, boolean, ragged, or extreme inputs fall back to the canonical scalar implementation.

Equivalence tests compare every score component against repeated scalar evaluation with an absolute tolerance of `0.000001`, matching the public six-decimal score precision. They cover deterministic randomized inputs, flat and inverted paths, batch sizes on both sides of boundaries, near-threshold cases, close ranks, and a larger multi-symbol scan. Existing scanner and API tests continue to exercise self-match exclusion, overlap suppression, final-window behavior, and response compatibility.

Local deterministic results for a 30-bar reference and 1,000 bars per candidate were:

| Symbols | Windows | Phase 8 scalar | Phase 9 batch | Speedup |
| ---: | ---: | ---: | ---: | ---: |
| 100 | 97,100 | 18.120 s | 1.394 s | 13.0× |
| 500 | 485,500 | 88.888 s | 6.531 s | 13.6× |
| 1,000 | 971,000 | not recorded | 12.853 s | — |

The 100-symbol scan used 4.2 MiB peak traced Python memory. In a separate end-to-end diagnostic, SQLite returned and materialized 100,000 cached bars in 1.111 seconds and the subsequent scan took 1.241 seconds. Database reads, domain-object materialization, and six-decimal public-score rounding/object construction are therefore the main remaining local costs. Safety limits remain 5,000 symbols and 2,000,000 estimated windows; Phase 9 does not add multiprocessing or change the API/UI.

## Backend packages

- `core`: provider-neutral domain models and protocols for OHLCV market data, similarity analysis, and persistence.
- `application`: `MarketDataService` computes missing coverage, refreshes the recent edge, chunks requests, and coordinates atomic persistence.
- `infrastructure`: `TwelveDataProvider` owns provider HTTP behavior; `SQLiteMarketDataRepository` owns relational storage and migrations.
- `api`: transport concerns such as FastAPI routes and response schemas.

## Intended extension points

`MarketDataProvider.get_historical_bars` retrieves normalized external data. `MarketDataRepository` stores and queries normalized bars plus synchronization coverage. `MarketDataService` depends only on these protocols. `SimilarityEngine` isolates numerical analysis, while `SimilarityResultRepository` remains the boundary for future result storage.

`real_market_validation` is a developer-only application utility. It depends on the same market-data loader and scanner contracts as the API, measures loading separately from scanning, and serializes stable summaries without exposing provider requests or credentials. Its data-quality diagnostics report suspicious input but do not mutate, fill, or reject valid market gaps. The CLI composes this utility with the production Twelve Data and SQLite adapters; tests compose it with fixtures, so normal verification never requires network access.

Phase 10 live validation found two non-scoring boundary defects. The provider's 16,412-row stock response contained one blank-name row; the adapter now skips isolated malformed catalog rows while continuing to reject a catalog with no valid records. Daily cache coverage stored with exchange-local boundaries could also be less than one interval offset from an equivalent UTC UI range. Scan readiness now mirrors `MarketDataService` gap semantics: a sub-interval initial offset is covered, while a missing full bar still makes the symbol ineligible. Both behaviors have focused regression tests.

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

Phase 11 adds an outer request-safety boundary with a bounded body, correlation ID, safe unexpected-error envelope, and concise lifecycle logging. Similarity admission and response deadlines are process-local. CPU scoring runs off the event loop; already-admitted work retains its slot and finishes safely after a timed-out response, preventing timeout-driven worker accumulation. Cache and universe refresh locks coalesce duplicate in-process work. SQLite readiness verifies integrity and exact migration state without making provider availability a startup dependency.

## Phase boundary

The v0.2 development branch adds conservative request-driven named-universe hydration without changing Similarity Engine V1, overlap rules, ranking, or self-match semantics. Statistical calibration, session-aware intraday windows, automatic background/full-market hydration, authoritative index membership, result persistence, prediction, distributed workers, and authentication remain out of scope.
