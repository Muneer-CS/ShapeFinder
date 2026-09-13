# ShapeFinder

Find historical stock charts with similar price patterns.

ShapeFinder is a local-first research application that compares a reference price chart with earlier chart windows across selected stocks. It ranks descriptive shape similarity; it does not forecast prices or produce trading signals.

https://shapefinder-frontend-production.up.railway.app/

## What it does

1. Choose a stock, date range, and daily or intraday interval as the **reference** pattern.
2. Choose a separate **search period** and either a custom ticker list or cached stock **universe**.
3. Search historical windows and review ranked ShapeFinder similarity scores.
4. Load match previews, inspect score components, and compare actual-price charts with a rebased-to-100 overlay.

## Features

- Daily and intraday (`1min`, `5min`, `15min`, `30min`, `1h`, `1day`) reference charts
- Custom searches for up to 10 tickers
- Cached-only U.S. Stocks, NASDAQ, and NYSE universe searches
- Ranked results with Shape, Direction, Path, and Amplitude components
- Truthful named-universe coverage and stale-cache reporting
- Lazy match previews, selected comparison charts, and a normalized overlay
- SQLite OHLCV and universe caching with transactional migrations
- Safe API errors, request IDs, readiness/liveness checks, CORS validation, and bounded scans
- Responsive, keyboard-accessible React interface with stale-request protection

## Architecture

```text
React + TypeScript + Vite + Recharts
                    │ HTTP
FastAPI routes → application services → provider-neutral core
                    ├─ Twelve Data adapter
                    ├─ SQLite repository
                    └─ NumPy similarity scanner
```

The domain and application layers do not depend on Twelve Data or SQLite. See [docs/architecture.md](docs/architecture.md) for boundaries, data flow, cache behavior, and extension points.

## Requirements

- Node.js 20+
- Python 3.11+
- A Twelve Data API key for uncached live market data

## Local setup

Clone the repository, then create a local environment file:

```bash
cp .env.example .env
```

On Windows PowerShell, use `Copy-Item .env.example .env`. The `.env` file is ignored by Git.

### Backend

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -r requirements-dev.lock
python -m pip install -e . --no-deps
uvicorn shape_finder.main:app --reload
```

The API starts at `http://localhost:8000` by default.

### Frontend

In another terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173`.

## Twelve Data setup

Set `TWELVE_DATA_API_KEY` in the repository-root `.env` file. It is read only by the backend.

Never put the key in Git, frontend code, `frontend/.env*`, or any variable prefixed with `VITE_`. With no key, the application still starts and fully cached requests still work; uncached market-data requests return a safe `PROVIDER_NOT_CONFIGURED` response.

Twelve Data quotas, supported exchanges, and historical availability depend on the configured account. ShapeFinder retries transient provider/network failures once but does not retry authentication, validation, rate-limit, or malformed-response failures.

## Configuration

`.env.example` documents every supported variable. Important production differences are:

- `APP_ENV=production`
- `API_HOST` must be reachable by the hosting runtime.
- `CORS_ORIGINS` must be a JSON list of exact frontend HTTP(S) origins; wildcards are rejected.
- `DATABASE_PATH` must point to persistent storage.
- `TWELVE_DATA_API_KEY` must be injected as a backend secret.
- The frontend build should set `VITE_API_BASE_URL` to the public backend origin, or omit it for a same-origin reverse proxy.

See [deployment readiness](docs/deployment-readiness.md) before choosing a hosting provider.

## Similarity methodology

Similarity Engine V1 compares normalized closing-price sequences with fixed weights:

| Component | Weight | Meaning |
| --- | ---: | --- |
| Shape | 45% | Overall contour after normalization |
| Direction | 30% | Agreement in up/down movement |
| Path | 20% | Point-by-point fitted path error |
| Amplitude | 5% | Relative size of the move |

The overall result is a ShapeFinder similarity score from 0 to 100. It is not a probability, confidence estimate, or prediction. Exact formulas and numerical policies are documented in [docs/architecture.md](docs/architecture.md#similarity-engine-v1).

## Stock universes

Universe metadata comes from Twelve Data's stock reference endpoint and is conservatively filtered to active U.S. common stocks. ETFs, ADRs, preferred shares, warrants, rights, funds, REITs, and other instrument classes are excluded.

Named-universe searches are intentionally cached-only: candidates without complete local history for the requested interval and period are skipped rather than fetched unexpectedly. Results report the known, eligible, scanned, and skipped counts so partial coverage is explicit. Custom searches may fetch missing data normally.

## Data and cache behavior

The default database is `backend/data/shapefinder.sqlite3` when the backend is started from `backend/`; override it with `DATABASE_PATH`. SQLite database, WAL, and shared-memory files are ignored by Git.

Complete cached ranges avoid provider calls. Missing ranges are fetched in bounded chunks and written transactionally. Recent ranges refresh a small trailing edge so active candles can be corrected. Universe metadata is refreshed on demand and reused for `UNIVERSE_TTL_HOURS` (24 by default).

For deployment, the database must live on a persistent volume. Losing it does not change similarity correctness once data is fetched again, but it removes cached market/universe data, can increase quota use and latency, and leaves named universes with no scan-ready histories until repopulated.

## API health

- `GET /api/v1/health` — process liveness; independent of provider configuration
- `GET /api/v1/readiness` — SQLite accessibility, integrity, and migration readiness
- `GET /api/v1/universes` — available universe summaries and freshness
- `GET /api/v1/market-data/{symbol}` — normalized OHLCV history
- `POST /api/v1/similarity/search` — ranked historical matches

Market-data and similarity timestamps must be timezone-aware ISO 8601 values.

## Testing

Backend:

```bash
cd backend
ruff check .
ruff format --check .
mypy src
pytest
```

Frontend:

```bash
cd frontend
npm ci
npm run check
npm audit
```

Real-provider validation is optional and quota-conscious. See [docs/real-market-validation.md](docs/real-market-validation.md).

## Security

- Keep `TWELVE_DATA_API_KEY` backend-only and untracked.
- Public API errors contain safe codes and request IDs, not provider payloads, SQL details, stack traces, paths, or credentials.
- CORS accepts explicit origins only.
- Request bodies, concurrent scans, scan duration, candidate count, and estimated window count are bounded.
- `.env`, SQLite files, build output, validation output, caches, and temporary artifacts are ignored.

Review [docs/release-checklist.md](docs/release-checklist.md) before publication or deployment.

## Limitations

- Twelve Data is the only live provider adapter currently implemented.
- Named universes search only scan-ready local histories; there is no background hydration.
- Intraday candidate windows may span overnight or session boundaries.
- SQLite and scan admission are process-local and are not designed for distributed multi-worker writes.
- Provider availability and quotas still apply.
- ShapeFinder does not include authentication, user accounts, or persistent search jobs.

## Disclaimer

ShapeFinder compares historical price-chart shapes. It does not predict future prices, provide investment advice, or guarantee future similarity or performance. Use it as a research tool and make financial decisions independently.

## License

ShapeFinder is available under the [MIT License](LICENSE).

## GitHub metadata

Recommended description: **Find historical stock charts with similar price patterns.**

Suggested topics: `stocks`, `finance`, `fastapi`, `react`, `typescript`, `python`, `similarity-search`, `data-visualization`.
