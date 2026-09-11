# Architecture boundaries

## Dependency direction

Dependencies point inward: API and infrastructure depend on application/core contracts, never the reverse. The frontend knows only the public HTTP contract.

## Frontend flow

The React form holds two distinct ranges. The reference range is converted to timezone-aware API boundaries and sent through the typed `marketData` client. The search range is validated and captured with the successful reference result, but remains local session state for future similarity work.

```text
reference form → typed HTTP client → FastAPI route → MarketDataService
                                                      ├── SQLite cache
                                                      └── Twelve Data (when needed)
API bars → exact close values → responsive Recharts line chart
search range ─────────────────→ local future-search context only
```

Daily inputs use calendar-day boundaries; intraday inputs use the browser's local date-time control and are serialized as UTC ISO 8601 values. Public API error codes are mapped to safe, actionable UI messages without exposing provider payloads or credentials.

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

V1 assumes observations are ordered and reasonably cover comparable start-to-end periods. Because its input contains closes rather than timestamps, interpolation treats observations as evenly spaced along each period. It uses no volume, candlestick features, indicators, fundamentals, sectors, news, or machine learning. It does not scan or rank the market and makes no predictions. The engineered weights and practical score thresholds are deterministic but not statistically validated; later real-market evaluation may refine them.

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

Phase 5 adds deterministic comparison of two supplied close-price sequences. Historical scanning, real-market match ranking, similarity-result APIs and UI, prediction, background jobs, and authentication remain out of scope.
