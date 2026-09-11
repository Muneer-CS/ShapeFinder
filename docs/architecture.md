# Architecture boundaries

## Dependency direction

Dependencies point inward: API and infrastructure depend on application/core contracts, never the reverse. The frontend knows only the public HTTP contract.

## Backend packages

- `core`: provider-neutral domain models and protocols for OHLCV market data, similarity analysis, and persistence.
- `application`: use cases that coordinate core contracts. Phase 1 intentionally contains no search workflow.
- `infrastructure`: concrete external adapters. `TwelveDataProvider` owns HTTP query construction, provider errors, response validation, and conversion into core models.
- `api`: transport concerns such as FastAPI routes and response schemas.

## Intended extension points

`MarketDataProvider.get_historical_bars` retrieves a normalized `TimeSeries` for a symbol, interval, and exact time range. `MarketDataService` enforces provider-independent date and symbol rules. `SimilarityEngine` and `SimilarityResultRepository` continue to isolate future numerical analysis and storage.

## Timestamp policy

API callers must send timezone-aware boundaries. For intraday intervals, the adapter converts boundaries to UTC, requests `timezone=UTC`, and attaches UTC to returned timestamps. For daily data, Twelve Data ignores the timezone parameter; ShapeFinder sends calendar dates and attaches the IANA `exchange_timezone` returned in provider metadata. Missing or unknown provider timezone metadata is rejected rather than guessed.

The endpoint returns bars in chronological order. Non-trading periods naturally contain no bars, and a completely empty result becomes a typed `NO_DATA` response.

## Failure boundary

Twelve Data-specific error bodies are translated to stable internal exceptions. The API maps those exceptions to consistent public codes without forwarding raw payloads, stack traces, request URLs, or credentials. A shared async HTTP client is owned by the FastAPI lifespan.

## Phase boundary

Phase 2 provides on-demand market-data retrieval only. Persistence, similarity metrics, historical scanning, charting, prediction, and authentication remain out of scope.
