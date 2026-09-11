# ShapeFinder

ShapeFinder is the foundation for a stock-chart similarity application. The future product will compare normalized chart behaviour across securities and historical periods; it is not a forecasting or trading-recommendation tool.

## Current scope: Phase 4

ShapeFinder now provides a polished reference-chart workflow on top of the local normalized market database and provider abstraction. A user can choose a ticker, reference period, future search boundary, and interval, then load an exact closing-price chart from the existing API. Reference dates define the chart being studied; search dates are retained separately for the later matching phase and are not sent to the market-data endpoint. ShapeFinder still does **not** scan history, calculate similarity, show match scores, predict prices, or make recommendations.

## Architecture

```text
frontend (React UI + typed API client + Recharts)
        │ HTTP
backend API routes
        │
application services
        ├── market-data repository protocol → SQLite
        ├── market-data provider protocol → Twelve Data
        ├── similarity engine protocol
        └── repository protocol
```

The core layer contains provider-neutral domain types. `MarketDataService` coordinates `MarketDataRepository` and `MarketDataProvider`; it does not import SQLite or Twelve Data. This keeps both the data vendor and database replaceable, including a later migration to PostgreSQL.

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

The market-data endpoint requires timezone-aware ISO 8601 `start` and `end` values. Supported intervals are `1min`, `5min`, `15min`, `30min`, `1h`, and `1day`.

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

The workflow supports daily date inputs and timezone-aware intraday date-time inputs for `1min`, `5min`, `15min`, `30min`, `1h`, and `1day`. It validates the ticker and both ranges before requesting data, shows loading and safe failure states, and replaces the previous reference chart after a successful request. The chart uses the API's exact closing values without normalization or similarity processing.

The search period is deliberately session-only UI state in Phase 4. It appears beside the loaded reference so the distinction is visible, but it is not persisted and does not initiate a search.

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
