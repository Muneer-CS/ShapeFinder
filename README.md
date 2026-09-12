# ShapeFinder

ShapeFinder is the foundation for a stock-chart similarity application. The future product will compare normalized chart behaviour across securities and historical periods; it is not a forecasting or trading-recommendation tool.

## Current scope: Phase 8

ShapeFinder now supports provider-independent stock universes in addition to the existing custom list of up to 10 tickers. Users can select U.S. common stocks, NASDAQ common stocks, NYSE common stocks, or Custom. Broad searches are deliberately cached-only: ShapeFinder scans the members with complete local history and reports total, eligible, scanned, and skipped counts rather than implying full coverage. Match charts remain lazy and comparison behavior is unchanged. ShapeFinder still does **not** hydrate an entire market, predict prices, or make recommendations.

## Architecture

```text
frontend (React UI + typed API client + Recharts)
        │ HTTP
backend API routes
        │
application services
        ├── market-data repository protocol → SQLite
        ├── market-data provider protocol → Twelve Data
        ├── chart similarity engine → deterministic close-price analysis
        ├── historical scanner → windowing, ranking, overlap suppression
        └── repository protocol
```

The core layer contains provider-neutral domain types. `MarketDataService` coordinates `MarketDataRepository` and `MarketDataProvider`; it does not import SQLite or Twelve Data. This keeps both the data vendor and database replaceable, including a later migration to PostgreSQL.

`ChartSimilarityEngine` implements the core `SimilarityEngine` protocol without importing FastAPI, SQLite, Twelve Data, React, or HTTP. It accepts two close-price sequences and returns the overall score plus shape, direction, fitted-error, and amplitude components. See [the architecture notes](docs/architecture.md#similarity-engine-v1) for the exact formula and policies.

## Project layout

- `frontend/` — React, TypeScript, Vite, Recharts, Vitest, ESLint, and Prettier
- `backend/src/shape_finder/` — FastAPI entrypoint and separated core, application, API, and infrastructure packages
- `backend/tests/` — backend API tests
- `docs/architecture.md` — boundaries and future extension points
- `.env.example` — safe local configuration template

## Local development

Prerequisites: Node.js 20+ and Python 3.11+.

### Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
uvicorn shape_finder.main:app --reload
```

The API is available at `http://localhost:8000`.

- `GET /api/v1/health` — configuration-independent service health
- `GET /api/v1/market-data/{symbol}?start=...&end=...&interval=...` — normalized OHLCV history
- `POST /api/v1/similarity/search` — ranked historical matches for up to 10 explicit candidate symbols
- `GET /api/v1/universes` — available provider-derived universe summaries and freshness

The market-data endpoint requires timezone-aware ISO 8601 `start` and `end` values. Supported intervals are `1min`, `5min`, `15min`, `30min`, `1h`, and `1day`.

The similarity endpoint remains backward-compatible with a `search.symbols` custom list. Broad mode instead sends `search.universe.kind` as `us_equities`, `nasdaq`, or `nyse`. It returns the loaded reference summary, enforced search range, ranked matches with score components, and truthful coverage statistics. All timestamps must include a timezone.

## Stock universes and broad scans

Universe metadata comes from Twelve Data's official [`/stocks` reference-data endpoint](https://support.twelvedata.com/en/articles/5620513-how-to-find-all-available-symbols-at-twelve-data). The adapter requests U.S. common stocks and the application independently applies a conservative policy: country must be U.S., type must be exactly `Common Stock`, and the record must be active when active status is supplied. ETFs, ADRs/depositary receipts, preferred shares, warrants, rights, funds, REITs, and other instrument classes are excluded. Duplicate tickers are collapsed deterministically, preferring NASDAQ and then NYSE metadata.

The normalized catalog is replaced transactionally in SQLite after a successful refresh and reused for 24 hours by default (`UNIVERSE_TTL_HOURS`). A successful refresh removes symbols absent from the new snapshot. If refresh fails, an existing stale snapshot remains usable and the API/UI marks it stale; with neither a cache nor provider access, universe metadata returns a safe provider error. S&P 500 membership is deferred because Twelve Data's supported-stock list does not provide authoritative index membership, and Phase 8 does not scrape or embed an unmaintainable list.

A broad candidate is scan-ready only when synchronization coverage spans the requested search range for the exact interval and at least as many cached bars exist as in the loaded reference. Readiness uses batched coverage/count queries rather than loading every bar. Broad searches never fetch missing candidate histories in Phase 8; only the reference may use the normal cache-aware provider path. This cached-only default consumes no surprise quota. Bounded hydration is intentionally deferred until quota budgeting and an explicit UX can be designed together.

Requests are limited to 5,000 resolved universe symbols and an estimated 2,000,000 windows. Each symbol's passing windows are overlap-suppressed and reduced to at most `top_n` finalists before global ranking. This is equivalent to Phase 6 ranking because overlap suppression is symbol-local, while bounding the cross-symbol candidate pool. The similarity formula and weights are unchanged.

Local deterministic benchmarks (Python 3.12 on the development machine, 30-bar reference, 1,000 bars per candidate) measured 97,100 windows across 100 symbols in 18.120 seconds (5,359 comparisons/second), and 485,500 windows across 500 symbols in 88.888 seconds (5,462 comparisons/second). These are observations, not CI timing guarantees.

## Local market database

SQLite is the initial persistence adapter. By default, the backend creates `data/shapefinder.sqlite3`; override this with `DATABASE_PATH`. Database, WAL, and shared-memory files are ignored by Git.

Schema changes use small, ordered application migrations recorded in `schema_migrations`, so upgrades do not require deleting the database. OHLCV values are stored as decimal strings to preserve exact provider precision. The composite primary key `(symbol, interval, timestamp_utc)` both prevents duplicates and supports chronological range queries. Successful synchronization ranges are recorded separately, including source and synchronization time.

Writes use one transaction and batch upserts. A repeated or revised provider bar updates the existing row, allowing current data and corrections to replace stale values without duplicates.

### Synchronization and freshness

- Complete historical coverage is returned entirely from SQLite without calling the provider.
- Missing regions are requested independently, then committed together only after all provider calls succeed.
- Recent requests refresh only the last three interval periods. This conservatively revisits an active daily candle or latest intraday bars without re-fetching older history.
- If the cache is complete but Twelve Data is unconfigured, cached data is still returned—even for a recent range. Missing data still returns `PROVIDER_NOT_CONFIGURED`.
- Requests are deterministically split into chunks capped at 4,500 theoretical interval points, safely below Twelve Data's 5,000-point limit. Chunk boundaries advance by exactly one interval to avoid gaps or duplicate boundary bars.

This is intentionally not a full exchange-calendar model. Non-trading gaps are represented by synchronization coverage rather than fabricated bars, and recent-edge refreshes allow incomplete candles to be corrected later.

## Twelve Data configuration

Copy `.env.example` to an untracked `.env` and set `TWELVE_DATA_API_KEY` for live market data. The key is read only by FastAPI and must never use a `VITE_` prefix. With no key, the application and health route still start normally; market-data requests return `503 PROVIDER_NOT_CONFIGURED`.

The provider adapter:

- sends intraday boundaries and requests in UTC;
- returns intraday timestamps as timezone-aware UTC datetimes;
- treats daily boundaries as calendar dates and uses Twelve Data's `exchange_timezone` metadata to localize daily timestamps;
- preserves price and volume precision using decimals;
- retries transient network and server failures once, but never retries invalid requests, authentication failures, or rate limits;
- returns at most the data supplied by one Twelve Data response.

Twelve Data documents a maximum of 5,000 points per time-series request. Data availability, freshness, exchanges, and request quotas depend on the configured account tier. Streaming and background synchronization are not used in this phase.

### Frontend

In a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Set `VITE_API_BASE_URL` in an untracked `.env` if the API uses a different origin.

The workflow supports daily date inputs and timezone-aware intraday date-time inputs for `1min`, `5min`, `15min`, `30min`, `1h`, and `1day`. It validates the ticker and both ranges before requesting data, shows loading and safe failure states, and replaces the previous reference chart after a successful request. The reference chart uses the API's exact closing values without normalization or similarity processing.

Reference loading and similarity search are intentionally separate actions. Search controls are enabled only after a reference is loaded; changing a reference input invalidates that loaded reference and its results, while changing search settings clears only stale results. Candidate tickers are normalized, de-duplicated chips capped at 10. The ranked result cards show a one-decimal engineered score, exact period and interval, expandable component details, and aggregate scan statistics.

The search-scope selector explains that a universe controls which stocks are considered, while the search period controls which dates are inspected. Custom mode retains ticker chips. Named-universe results display cached coverage prominently, including partial and zero-ready states.

Match previews request their exact symbol, start, end, and interval only after the user asks to view one or selects it for comparison. A preview failure leaves the ranked result usable. The comparison workspace labels the side-by-side charts as actual prices. Its optional overlay rebases each series to 100 solely for visual comparison; that visualization is not the backend scoring normalization, a probability, or a forecast.

## Quality checks

```bash
cd frontend
npm run check

cd ../backend
ruff check .
ruff format --check .
mypy src
pytest
```

## Security notes

Market-data credentials are backend-only and represented as secret configuration values. `.env` files are ignored. No credential is needed to start or test the application.
