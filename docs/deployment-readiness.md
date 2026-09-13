# Deployment readiness

ShapeFinder 0.1.0 is prepared as a local release candidate. This document records requirements for a later public deployment; it does not select a provider or authorize deployment.

## Expected topology

Deploy the Vite production build as static assets and run the FastAPI service as a separate backend. The frontend may call the backend through either:

- a same-origin reverse proxy, where `/api/v1/*` is forwarded to FastAPI; or
- a separate HTTPS API origin configured at frontend build time.

Terminate public TLS at the hosting platform or reverse proxy. Do not expose a development Vite server or use Uvicorn `--reload` in production.

## Frontend requirements

Build with Node.js 20+:

```bash
cd frontend
npm ci
npm run build
```

Serve the generated `frontend/dist/` directory as immutable/static content. The application has a single route and requires no server-side rendering.

`VITE_API_BASE_URL` is a build-time public value:

- omit it for a same-origin API proxy;
- set it to the backend's absolute HTTPS origin for a split-origin deployment.

The value cannot contain credentials, query parameters, or a fragment. Never place `TWELVE_DATA_API_KEY` or any other secret in a `VITE_*` variable because Vite embeds such values in browser JavaScript.

## Backend requirements

- Python 3.11+
- dependencies from `backend/requirements-dev.lock` for the current reproducible release workflow
- an installed editable/wheel package (`python -m pip install -e . --no-deps` locally)
- a writable, persistent directory for SQLite
- outbound HTTPS access to Twelve Data
- an HTTPS reverse proxy or platform ingress for public traffic

Single-process startup command:

```bash
uvicorn shape_finder.main:app --host 0.0.0.0 --port 8000
```

The current SQLite repository, refresh locks, and scan admission controls are process-local. Start with one backend worker. Multiple workers or replicas can independently admit scans and write the same database and therefore require a future storage/concurrency design review.

## Environment variables

| Variable | Production guidance |
| --- | --- |
| `APP_ENV` | Set to `production`. |
| `LOG_LEVEL` | Usually `INFO`; use platform log collection. |
| `API_HOST` | Bind value used by operational tooling; typically `0.0.0.0`. |
| `API_PORT` | Port exposed internally by the service. |
| `CORS_ORIGINS` | JSON list of exact frontend origins, for example `["https://app.example.com"]`. Wildcards are rejected. |
| `TWELVE_DATA_API_KEY` | Backend secret injected by the deployment platform. Required for uncached live data. |
| `TWELVE_DATA_BASE_URL` | Keep `https://api.twelvedata.com` unless intentionally testing an adapter-compatible endpoint. Production requires HTTPS. |
| `MARKET_DATA_TIMEOUT_SECONDS` | Provider request timeout; default `10`. |
| `DATABASE_PATH` | Absolute path on a persistent volume, for example `/var/lib/shapefinder/shapefinder.sqlite3`. |
| `UNIVERSE_TTL_HOURS` | Universe metadata cache TTL; default `24`. |
| `UNIVERSE_HYDRATION_MAX_SYMBOLS` | Maximum daily symbols hydrated by one named-universe search; default `5`, hard maximum `25`. |
| `UNIVERSE_HYDRATION_INTRADAY_MAX_SYMBOLS` | More conservative intraday batch maximum; default `2`, hard maximum `10` and capped by the daily value. |
| `UNIVERSE_HYDRATION_TIMEOUT_SECONDS` | Per-request hydration time budget before scanning cached/ready symbols; default `15`. |
| `SCAN_TIMEOUT_SECONDS` | Request response deadline for scans; default `60`. |
| `MAX_CONCURRENT_SCANS` | Per-process scan admission cap; default `2`. |
| `MAX_REQUEST_BODY_BYTES` | Request-body cap; default `65536`. |
| `VITE_API_BASE_URL` | Frontend build-time public API origin; never a backend secret. |

`.env` is a local-development convenience only. Production values should come from the hosting platform's secret and environment configuration.

## Persistent SQLite requirement

The file identified by `DATABASE_PATH` contains both market-data and universe caches. Its adjacent `-wal` and `-shm` files may exist while the service is running and must share the same writable filesystem.

Use a persistent volume or durable filesystem if cached data must survive restarts, redeploys, or rescheduling. Many serverless/container platforms provide only ephemeral root filesystems; placing the database there will erase it during normal lifecycle events.

Database loss does not silently change the similarity formula or corrupt source prices. Custom requests can fetch data again, so correctness is recoverable. It does, however:

- discard all cached OHLCV and universe metadata;
- increase startup/user latency and Twelve Data quota consumption;
- reset named-universe scan coverage until request-driven bounded hydration repopulates histories;
- remove the application's main protection against repeated provider requests.

Back up the persistent database according to the chosen platform's volume policy. Do not share one SQLite file across network filesystems or multiple replicas without validating locking semantics.

## CORS and API routing

For split origins, set `CORS_ORIGINS` to the exact public frontend origin or origins. Include the scheme and port where non-default. Do not use `*`. The backend allows only the browser methods and headers required by this application.

For same-origin deployment, route `/api/v1/*` from the public frontend host to FastAPI and still configure the exact public origin. Forward request IDs and preserve `X-Request-ID` response headers through the proxy.

## Health and readiness

- `GET /api/v1/health` is liveness. It does not require Twelve Data or database readiness.
- `GET /api/v1/readiness` checks SQLite access, integrity, and migration state.

Use health for process restarts and readiness to decide whether traffic should be sent. Provider availability is intentionally not a readiness dependency; provider failures are surfaced safely per request.

## Twelve Data secret handling

Configure `TWELVE_DATA_API_KEY` only in the backend deployment's secret store/environment. It must never be committed, stored in GitHub variables intended for the frontend, prefixed with `VITE_`, passed as a frontend build argument, written into static assets, or logged.

Before publication and every deployment, scan tracked files, Git history, and `frontend/dist/` for the actual key and backend-only variable values. Rotate the credential immediately if it is ever exposed.

## Remaining steps before deployment

1. Select an open-source license before or at GitHub publication.
2. Publish the clean repository without local `.env`, databases, caches, build output, or validation artifacts.
3. Choose static frontend hosting, a Python backend runtime, and durable volume storage.
4. Configure a backend secret for Twelve Data and explicit production CORS origins.
5. Decide between same-origin proxying and a split API origin, then build the frontend with the matching `VITE_API_BASE_URL`.
6. Install from a clean checkout and run the complete release checklist.
7. Provision HTTPS, log retention, database backup/restore, and platform health checks.
8. Run a quota-conscious production smoke test after deployment.

No GitHub repository, cloud resource, deployment, release, or tag is created by this document.
