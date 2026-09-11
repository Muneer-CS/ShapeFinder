# Architecture boundaries

## Dependency direction

Dependencies point inward: API and infrastructure depend on application/core contracts, never the reverse. The frontend knows only the public HTTP contract.

## Backend packages

- `core`: provider-neutral domain models and protocols for OHLCV market data, similarity analysis, and persistence.
- `application`: `MarketDataService` computes missing coverage, refreshes the recent edge, chunks requests, and coordinates atomic persistence.
- `infrastructure`: `TwelveDataProvider` owns provider HTTP behavior; `SQLiteMarketDataRepository` owns relational storage and migrations.
- `api`: transport concerns such as FastAPI routes and response schemas.

## Intended extension points

`MarketDataProvider.get_historical_bars` retrieves normalized external data. `MarketDataRepository` stores and queries normalized bars plus synchronization coverage. `MarketDataService` depends only on these protocols. `SimilarityEngine` and `SimilarityResultRepository` continue to isolate future numerical analysis and result storage.

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

Phase 3 provides on-demand cached market-data retrieval only. Similarity metrics, historical scanning, charting, prediction, background jobs, and authentication remain out of scope.
