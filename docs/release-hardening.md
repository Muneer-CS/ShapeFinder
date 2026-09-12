# Release hardening

## Audit findings and controls

Phase 11 retained the existing architecture and numerical semantics. The audit found strong provider/domain separation, transactional cache writes, bounded provider retries, typed input models, and an existing frontend abort path. The release gaps were unvalidated environment values, no global safe-500 response, no correlation ID, liveness without readiness, duplicate concurrent refresh work, synchronous CPU scoring on the event loop, no render error boundary, incomplete preview cancellation, and floating frontend dependency ranges.

The resulting controls are deliberately local and lightweight:

- startup validates environment, ports, TTLs, timeouts, request size, scan concurrency, database path, and explicit CORS origins;
- wildcard CORS is rejected, and production requires at least one configured origin;
- every response receives `X-Request-ID`; public errors repeat it in a stable error envelope;
- POST/PUT/PATCH bodies are capped at `MAX_REQUEST_BODY_BYTES` (64 KiB by default);
- similarity requests use a process-local `MAX_CONCURRENT_SCANS` admission limit and `SCAN_TIMEOUT_SECONDS` response deadline;
- NumPy scoring runs in a worker thread so the event loop remains responsive;
- same-symbol/interval cache misses and universe refreshes are coalesced with process-local locks;
- SQLite uses WAL, foreign keys, a 10-second busy timeout, normal synchronous mode, atomic writes, contiguous migrations, unknown-version refusal, and readiness integrity/migration checks;
- provider-normalized and persisted bars must have unique aware timestamps, finite positive OHLC prices, finite non-negative volume, and consistent high/low bounds;
- frontend reference, search, and preview requests are aborted on invalidation/unmount, and late responses are ignored even when a transport does not honor abort;
- a root React error boundary displays a safe reload fallback instead of a blank page.

## Environment

`APP_ENV` is `development`, `test`, or `production`; `LOG_LEVEL` is a standard uppercase level. `API_HOST`, `API_PORT`, `DATABASE_PATH`, `TWELVE_DATA_API_KEY`, `CORS_ORIGINS`, `UNIVERSE_TTL_HOURS`, and `MARKET_DATA_TIMEOUT_SECONDS` configure existing behavior. Phase 11 adds `SCAN_TIMEOUT_SECONDS`, `MAX_CONCURRENT_SCANS`, and `MAX_REQUEST_BODY_BYTES`. Copy `.env.example` to an ignored `.env` and keep the provider key backend-only.

Development defaults support Vite at `http://localhost:5173` and useful INFO logs. Production has no reload behavior, requires explicit non-wildcard CORS, keeps safe exception responses, and should set its host, origins, database path, and log level explicitly. `/health` is liveness only. `/readiness` verifies local SQLite integrity and current migrations but intentionally does not require Twelve Data to be online.

## Timeout and failure semantics

HTTP provider calls retain their configured timeout and a maximum of two attempts. Network and provider 5xx failures may retry once. Authentication, validation, malformed successful payloads, and rate-limit failures are never retried. Logs include only the provider endpoint name, attempt, status category, symbol/range metadata, and timings—never the API key, full query URL, response payload, or request body.

The scan deadline returns a bounded 504 response without forcibly terminating already-admitted work. Python cannot safely terminate a NumPy call in a worker thread, so the request task finishes in the background and retains its concurrency slot until it is done; this prevents timeout-driven thread accumulation. SQLite work uses independent bounded connections and atomic transactions, so a disconnected or timed-out request cannot expose a partial write. Browser aborts still prevent obsolete responses from updating frontend state.

## Dependencies

The frontend lockfile is the reproducible source used by `npm ci`. Direct dependencies are exact rather than `latest`, and Vite, TypeScript, and the React Vite plugin are development-only. Backend direct production and development ranges remain bounded by major version in `pyproject.toml`; the wheel metadata declares Python 3.11+ and application version 0.1.0. Audit results belong in the Phase 11 release report and should be refreshed for every release.

On 2026-09-12, npm reported zero known vulnerabilities across production and development dependencies. The first Python audit identified vulnerabilities only in the old local pip and pytest development tooling; pip was refreshed and the project moved from pytest 8 to the fixed pytest 9 line. Auditing `requirements-dev.lock` then reported zero known vulnerabilities. The production frontend build retains a non-blocking 612 kB bundle-size warning, primarily from the chart stack; code splitting is deferred because it is a performance optimization rather than a release-safety defect.

`python scripts/stress_sanity.py` runs repeated and concurrent fully cached searches against a temporary ignored SQLite database and reports timings plus retained/peak traced memory. `python scripts/benchmark_broad_scan.py --symbols 500 --bars 1000 --database` remains the larger scanner/database sanity. Neither tool contacts Twelve Data or defines a production capacity guarantee.

The Phase 11 workstation run completed 24 cached searches (including four concurrent) with 3,768 windows each in 17.161 seconds, zero failures, 0.25 MiB retained traced memory, and 11.73 MiB peak traced memory. A larger 1,000-symbol run evaluated 971,000 windows in 10.968 seconds. A separate 500-symbol database run scanned 485,500 windows in 5.699 seconds and loaded 500,000 SQLite rows in 5.863 seconds. Concurrent database/refresh regression tests reported no locking, partial writes, or duplicate provider work. These are controlled sanity observations, not load certification.

The final startup smoke returned 200 from liveness and readiness with version 0.1.0 and propagated a supplied request ID. A bounded AAPL daily UI load exercised the live provider path; logs showed endpoint names and attempt counts without rendered provider query URLs or credentials. Mobile-width browser inspection found no horizontal overflow, no unlabeled buttons, a coherent H1/H2 hierarchy, visible focus treatment, and no browser console warnings or errors.

## Limitations

This is a local-first 0.1.0 release, not a multi-tenant internet service. Per-process admission and locks do not coordinate multiple server processes. SQLite is not a distributed database. There are no users, authentication, background hydration, exchange-session-aware intraday windows, persisted result jobs, or automatic market-wide synchronization. Named universes remain cached-only. The engineered similarity score is descriptive and not a forecast, probability, or trading recommendation.
