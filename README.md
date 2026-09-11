# ShapeFinder

ShapeFinder is the foundation for a stock-chart similarity application. The future product will compare normalized chart behaviour across securities and historical periods; it is not a forecasting or trading-recommendation tool.

## Current scope: Phase 2

The foundation now includes a production-oriented Twelve Data adapter behind the provider-neutral market-data contract. The API can retrieve normalized OHLCV data on demand when configured. It does **not** scan history, calculate similarity, show match scores, cache market data, predict prices, or make recommendations.

## Architecture

```text
frontend (React UI)
        │ HTTP
backend API routes
        │
application services
        ├── market-data provider protocol
        ├── similarity engine protocol
        └── repository protocol
```

The core layer contains provider-neutral domain types. The Twelve Data implementation is injected behind `MarketDataProvider`, so provider response formats do not leak into services, API responses, or the UI. Database-specific code will remain behind repository interfaces, allowing a later move from SQLite to PostgreSQL.

## Project layout

- `frontend/` — React, TypeScript, Vite, Vitest, ESLint, and Prettier
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

## Twelve Data configuration

Copy `.env.example` to an untracked `.env` and set `TWELVE_DATA_API_KEY` for live market data. The key is read only by FastAPI and must never use a `VITE_` prefix. With no key, the application and health route still start normally; market-data requests return `503 PROVIDER_NOT_CONFIGURED`.

The provider adapter:

- sends intraday boundaries and requests in UTC;
- returns intraday timestamps as timezone-aware UTC datetimes;
- treats daily boundaries as calendar dates and uses Twelve Data's `exchange_timezone` metadata to localize daily timestamps;
- preserves price and volume precision using decimals;
- retries transient network and server failures once, but never retries invalid requests, authentication failures, or rate limits;
- returns at most the data supplied by one Twelve Data response.

Twelve Data documents a maximum of 5,000 points per time-series request. Data availability, freshness, exchanges, and request quotas depend on the configured account tier. Streaming is not used in this phase.

### Frontend

In a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Set `VITE_API_BASE_URL` in an untracked `.env` if the API uses a different origin.

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
