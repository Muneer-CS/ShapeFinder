# Architecture boundaries

## Dependency direction

Dependencies point inward: API and infrastructure depend on application/core contracts, never the reverse. The frontend knows only the public HTTP contract.

## Backend packages

- `core`: provider-neutral domain models and protocols for OHLCV market data, similarity analysis, and persistence.
- `application`: use cases that coordinate core contracts. Phase 1 intentionally contains no search workflow.
- `infrastructure`: future adapters for Twelve Data and database implementations. Current modules are placeholders with no network or database behavior.
- `api`: transport concerns such as FastAPI routes and response schemas.

## Intended extension points

`MarketDataProvider.get_historical_bars` describes the minimum future capability: retrieving typed OHLCV bars for a symbol, interval, and exact time range. `SimilarityEngine` and `SimilarityResultRepository` isolate numerical analysis and storage. Concrete adapters will be selected during application composition rather than imported into core logic.

## Phase boundary

Phase 1 provides only a health check. Provider calls, persistence, similarity metrics, historical scanning, charting, prediction, and authentication remain out of scope.

