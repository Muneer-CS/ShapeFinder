# ShapeFinder owner-only Railway preview

## Topology

The production frontend build uses relative API URLs. Railway terminates HTTPS
and sends traffic to Caddy. Before serving the app shell, static assets, or API
requests, Caddy checks the owner's HTTP Basic credentials with FastAPI over
the private network. FastAPI independently checks credentials on direct API
requests and rejects cross-origin writes. The only public health response is
`GET /__health`, which returns `ok` after startup. A broken authentication
upstream prevents access to the page and API. Close the authenticated browser
session when leaving a shared device.

## Preview setup for Prompt 22

Use the isolated **owner-preview** environment in **ShapeFinder v0.2 Dev**.
It has a separate domain, owner password, and volume. The public **ShapeFinder**
project's frontend and backend auto-deploy from `main`. Leave them in place
until the signed-in real-data preview passes.

| Service | Docker build root | Networking and deploy settings |
| --- | --- | --- |
| `shapefinder-preview-web` | `/frontend` | One HTTPS preview domain targeting port 3000. Health check `/__health`. |
| `shapefinder-preview-api` | `/backend` | Private network only. No public domain or public TCP proxy. One replica, health check `/__health`, new persistent volume at `/data`. Keep Serverless disabled when running scheduled refreshes. |

Use the Dockerfile in each root, with its default start command. Deploy the
verified commit from `release/owner-only-preview` only to the preview services.
Keep GitHub auto-deploy disabled during setup, and check both service source
settings before any push. Verify that neither existing production service
tracks the release branch. Confirm that only the owner has access to the
Railway preview project and its database, SSH access, backups, and variables.

See [Railway Dockerfiles](https://docs.railway.com/builds/dockerfiles),
[private networking](https://docs.railway.com/networking/private-networking/how-it-works),
[GitHub deploys](https://docs.railway.com/deployments/github-autodeploys),
[healthchecks](https://docs.railway.com/deployments/healthchecks), and
[Serverless](https://docs.railway.com/deployments/serverless).

### Environment variables

| Service | Name | Preview value |
| --- | --- | --- |
| Web | `PORT` | `3000` |
| Web | `SHAPEFINDER_PUBLIC_HOST` | Bare preview HTTPS hostname, without a scheme, port, or path |
| Web | `BACKEND_UPSTREAM` | `http://${{shapefinder-preview-api.RAILWAY_PRIVATE_DOMAIN}}:8000` |
| API | `APP_ENV` | `production` |
| API | `API_HOST` | `0.0.0.0` for Railway health checks and the preview private IPv4 network |
| API | `PORT` | `8000` |
| API | `SHAPEFINDER_PUBLIC_ORIGIN` | Exact HTTPS preview origin without a trailing slash |
| API | `SHAPEFINDER_OWNER_USERNAME` | A single owner username, letters, digits, `_`, `-`, or `.` |
| API | `SHAPEFINDER_OWNER_PASSWORD` | A unique randomly generated password, 24 to 128 printable ASCII characters without spaces |
| API | `DATABASE_PATH` | `/data/shapefinder-massive.sqlite3`, separate from the synthetic database |
| API | `CURRENT_DAILY_PROVIDER` | `massive` |
| API | `MASSIVE_BACKFILL_SESSIONS` | `63` |
| API | `MASSIVE_PUBLICATION_TIME_ET` | `00:05` Eastern on the calendar day after the trading session, including weekends and holidays. |
| API | `MASSIVE_REFRESH_ENABLED` | `false` until licensing is confirmed and the first backfill has passed |
| API | `MASSIVE_API_KEY` | API service secret, added by the owner once hosted data use is confirmed |

Use sealed API service variables for the password and data key. Keep
`TWELVE_DATA_API_KEY` unset in preview. Do not set an absolute
`VITE_API_BASE_URL` on the hosted frontend. Railway supplies the project,
environment, and volume-mount variables. The API refuses to start on Railway
if the declared database is outside the actual runtime mount or the owner
authentication settings are missing. See
[Railway service variables](https://docs.railway.com/variables) and
[Railway volumes](https://docs.railway.com/volumes).

### Backfill and refresh

Once hosted use is cleared, set `MASSIVE_API_KEY` and open a shell in the
**preview API service**. The initial fill runs in that service so it uses the
mounted database and the same request ledger.

```sh
python -B -m scripts.refresh_massive_daily
```

Repeat that command after an interruption. Refresh prunes candles older than
the configured window of at most 63 trading sessions before contacting Massive.
Successful grouped sessions are reused, pending sessions remain eligible, and the complete catalogue is replaced
only after all pages validate. The limiter allows at most five attempts per
rolling minute across grouped bars and catalogue pages. Avoid running backfills
against the same Massive key concurrently in separate environments, since
each volume has its own limiter. A missing session makes the command exit
nonzero. An individual symbol can still have a gap in a successfully fetched
batch, and the scan reports that exclusion.

Once backfill finishes, set `MASSIVE_REFRESH_ENABLED=true`. The scheduler in
the API process uses the exchange calendar and Eastern publication time. It
refreshes the latest eligible session and any missing history, records completion
on the same mount, and rechecks an incomplete refresh after 30 minutes. It
automatically resumes work after restart. Use the same volume for the SQLite
database, WAL, shared-memory file, and refresh marker. Do not run a separate
scheduled Railway service with a second volume for this database. A one-off
refresh uses the same command above. For authorized remote access, see
[Railway SSH](https://docs.railway.com/cli/ssh).

### Data approval and deployment checks

Before loading licensed prices, have the owner confirm that their Massive Stocks
Basic entitlement and applicable Nasdaq and NYSE terms cover this owner's
personal Railway deployment, automated scoring, OHLCV display, persistent
storage, and backup retention. Massive's individual terms restrict sharing and
professional use. They require removal of stored data when its licence ends
and limit retention for inactive backups, subject to stricter source terms.
Confirm volume capacity, backup availability, retention, and restoration in the
preview workspace before turning backups on. Until then, use an empty preview
or synthetic data. See [Massive market data terms](https://massive.com/legal/market-data-terms-of-service)
and [Railway volume backups](https://docs.railway.com/volumes/backups).

In preview, confirm HTTPS, a sign-in challenge on the app and direct public API
paths, and private API authentication from inside the network. Confirm that
the backend has no external URL or TCP proxy. After authorized backfill, sign
in, submit a cached daily scan, record the session and zero provider attempts,
and restart the API to verify data persistence. Promote to the existing public
URL only after preview verification and separate authorization.

## Local hosted configuration

`compose.preview.yaml` publishes the web service on loopback port 4181, leaves
the API unpublished, and uses a disposable named volume distinct from local
development. Set the required owner variables in the local shell and run
`docker compose -f compose.preview.yaml up --build`. Use synthetic data or a
disposable SQLite backup for parity checks. For native local testing, set
`APP_ENV=production`, `SHAPEFINDER_DATA_DIR` to an existing disposable directory,
and `DATABASE_PATH` to an absolute file inside it. This mode accepts an HTTP
loopback origin. Railway hosted mode requires HTTPS.
