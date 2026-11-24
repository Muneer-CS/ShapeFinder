# Stage 6B: manual current-pattern API

## Connected services

The existing FastAPI lifespan initializes the existing SQLite repository and creates
ManualPatternScanService over CurrentDataStore on that same database. No HTTP request,
catalogue refresh, hydration or scan is performed at startup. Tests can inject a
service, clock, calendar factory and mocked transport through create_app/service
construction. The historical routes and matching algorithms remain compatible.

GET `/api/v1/pattern-scans/capabilities` returns interval availability, reason codes,
timing basis, unfinished-mode availability, calendar implementation/version and date
coverage. It performs no provider requests. It exposes no API key, account scope,
filesystem paths or freeform operator provenance.

Default publication/finalization policies remain absent, so every current interval
is unavailable by default. A real scan needs evidence-backed timing policy, supported
listing metadata/calendar, adequate validated data, and a provider key when retrieval
is required. A provider key alone does not establish timing or entitlements.

Accepted configured policy requires CURRENT_ACCEPT_CONFIGURED_POLICIES=true. Responses
retain `status=configured_but_unverified` and `timing_basis=operator_configured`.
Policies administratively attested as verified use `timing_basis=operator_attested`.
Neither is silently represented as an independently provider-verified real-time
guarantee: provider_timing_guarantee_verified remains false. Unfinished mode remains
unavailable and requests for it fail explicitly before provider attempts.

### Local Twelve Data daily publication policy

To opt into the documented U.S. daily publication schedule locally, set
`CURRENT_ACCEPT_CONFIGURED_POLICIES=true` and include a policy like this in
`CURRENT_TIMING_POLICIES`:

```json
[
  {
    "interval": "1day",
    "next_trading_day_publication": true,
    "basis": "configured",
    "provenance": "https://support.twelvedata.com/en/articles/9935903-us-equities-market-data",
    "reviewed_at": "2026-11-04T00:00:00Z"
  }
]
```

The calendar selects the next exchange trading date. Its midnight Eastern time
is the earliest eligibility and retrieval boundary for the preceding session.
The scan still requires the expected completed candle and all required sessions
to be present and valid. A missing expected candle is reported as
`missing_expected_candle`, and the scanner does not substitute an older session.
Successful daily responses include `evaluated_session_dates` by symbol, even when
the evaluated stocks yield zero matches. This mode uses no fixed delay fields.
Intraday and unfinished-candle requirements are unchanged.

### Massive daily cache for Nasdaq and NYSE

Set `CURRENT_DAILY_PROVIDER=massive` for the backend to use the Massive cache
for completed daily scans of the Nasdaq or NYSE universe. The refresh command
reads `MASSIVE_API_KEY` from its process environment. The scanner does not need
that key. Run both against the same `DATABASE_PATH`.

`MASSIVE_BACKFILL_SESSIONS` defaults to the maximum of 63 exchange sessions.
The refresh removes candles older than that rolling window before making
provider requests. During backfill, choose a scan duration within the saved
history. `MASSIVE_PUBLICATION_TIME_ET` defaults to 00:05 Eastern on the next
calendar day, including Saturday for a Friday session. Massive documents
Stocks Basic REST availability after midnight Eastern. A scan still requires a
validated saved batch. See [Massive's Stocks Basic publication guidance](https://massive.com/knowledge-base/article/on-the-free-basic-plan-when-can-i-pull-the-previous-trading-days-data).

From the backend directory, run
`python -B -m scripts.refresh_massive_daily --refresh-catalogue` for the first
backfill and `python -B -m scripts.refresh_massive_daily` on later schedules.
Use one scheduled job per database. The command pages both exchange catalogues
before replacing the active snapshot. It refreshes the catalogue when its
`UNIVERSE_TTL_HOURS` expires and saves one split-adjusted grouped batch per missing
date. Failed batches remain pending, and reruns skip completed batches. The
shared SQLite request ledger limits catalogue and grouped requests together.
Schedule after your chosen publication threshold. A return code of 2 indicates
a failed refresh or pending dates.

The scan attempts every listing in the selected cached exchange in batches of
at most 128 stocks. It makes zero provider requests. The response counts scored,
stale, skipped, failed and unevaluated stocks. It retains finished evaluations
and labels coverage partial when a deadline, cancellation or computation budget
cuts the scan short. The candidate cap still applies to the Twelve Data path.

In all three modes, a stock's latest usable candle must fall on the latest
published trading session or either of the preceding two trading sessions.
Weekends and holidays do not count toward the lag. Only contiguous completed
windows ending at that candle are scored. An earlier gap can shorten the
available suffix. A stock is skipped if that suffix cannot supply enough
candles for any allowed alignment in the selected mode. If the latest window
fails the thresholds, the scanner does not search older windows for a match.

**Full** compares the entire drawing. **Forming** compares only its opening
portion, and all observed candles must already be completed. For an intended
duration of `D` candles and `N` observed candles, the scored portion and
reported estimated completion both use `(N - 1) / (D - 1)`. For example,
13 closed candles at an intended duration of 21 reach exactly 60%; 20 reach
95%. Forming requires `N < D`. Completion describes the fraction of the
hypothesized pattern duration and the similarity threshold independently
filters the shape score. Neither measures the chance of a future move.

Each ticker contributes at most one match. The scan ranks by latest end session,
then similarity, with the ticker as a stable final tie-breaker. For a ticker
with several qualifying alignments at the same endpoint, the highest similarity
wins. **All** considers both match types together and applies that rule once per
ticker. Exact similarity ties prefer higher completion. The response gives
`matched_start_session` and `last_candle_session` as inclusive exchange-local
dates, along with observed and intended candle counts. Massive rows use
separate SQLite tables from Twelve Data and are initialized by migration 5.

Each Massive comparison snapshot includes dated OHLCV observations from the exact
scored suffix in `comparison.observations`. Decimal strings preserve the stored
prices and volume. A forming comparison stops at the last observed candle. The
frontend can render the remaining reference from the submitted drawing and
`estimated_completion` without a history request or future-price data.

## POST /api/v1/pattern-scans

The canonical request from current-pattern-foundation.md is accepted, including
revisions and preview acknowledgement. Schema/hash/acknowledgement, settings and
capability validation precede provider attempts. Custom symbols must resolve to
trusted cached listing metadata. The cached catalogue and its refresh timestamp
are read in one SQLite read transaction and frozen once per scan.

An absent catalogue is `universe_catalog_unavailable`, not an empty successful scan.
This endpoint deliberately does not bootstrap/refresh the catalogue: establish it
through an explicit operator/legacy catalogue operation first. Legacy catalogue
traffic is outside current-scan accounting. A stale cached catalogue can be used,
but universe_snapshot.catalog_stale is true. This describes the cached membership
snapshot, not guaranteed contemporaneous complete market membership.

At admission one overall monotonic deadline is set (default 30 seconds). Admission
is immediate/bounded, shared with legacy scans through SearchAdmissionController;
full capacity returns 429. A single market cutoff is frozen once and passed unchanged
to every plan, history and match. Retrieval timestamps may be later; they never
advance that cutoff.

On the Twelve Data path, the default candidate slice is at most 200 stocks,
independently of top_n. SHA-256 ring ordering is stable for universe kind,
interval and frozen membership. A per-process
cursor advances by slice size so repeated scans progress. It resets on restart,
membership changes, or eviction after 128 catalogue contexts. This is suitable for
the current default single-worker deployment, not persistent multi-replica fairness.
Each scan truthfully counts all other frozen universe members as not_evaluated.

On that path, all selected stocks receive cache readiness checks first. Adequate warm stocks do
not spend provider attempts. Missing histories are hydrated only through the stage
6A run-bound gateway, with its daily/intraday attempt/symbol caps. No legacy provider
or UniverseService is called by this path. Catalogue HTTP cannot occur here; any
future addition must use the same dispatcher/run. Current scan traffic remains
separate from legacy/external API-key usage and account consumption guarantees.

Retrieval is bounded by both its configured subphase (default 20 seconds) and the
overall deadline. Calendar windows scale with interval/duration, but readiness still
requires the exact real session schedule; the window calculation never fabricates
trading sessions. Missing/stale histories cannot be treated as shorter listing history.

CPU scoring runs in an asyncio.to_thread worker with the same shape scoring
formula, overall deadline and hypothesis cap. The Twelve Data path also has a
default eight-second scoring subphase. The Massive path uses the overall
deadline for both cache reads and scoring.
Full/Forming filters precede per-stock selection; global top_n is applied after
unique-stock ranking. Interrupted stocks never contribute provisional matches.

Disconnect monitoring sets both HTTP cancellation and a threading event checked by
scoring batches. External route cancellation signals the service and defers admission
release until its task (including worker cleanup) finishes. Python threads cannot be
killed; worker helpers await their completion on cancellation. Small DB operations
may finish safely during cleanup. These are cooperative bounds, not hard process
termination guarantees. Deployment profiling and async execution remain required.

## Outcomes and HTTP statuses

| Status | Meaning |
| --- | --- |
| 200 evaluated | At least one stock fully evaluated; matches may legitimately be empty |
| 200 partial | At least one stock evaluated, but some frozen members were skipped/failed/not evaluated |
| 422 | Malformed request, invalid settings, revisions or acknowledgement; existing VALIDATION_ERROR envelope |
| 429 | Local scan admission full; existing SEARCH_CAPACITY_EXCEEDED envelope with Retry-After |
| 503 | Capability/catalogue/storage unavailable, or no stock could be fully evaluated |
| 504 | Deadline left no completed stock |
| 499 | Disconnect/cancellation left no completed stock; best-effort response if the client remains connected |

Operational errors use PatternScanErrorResponse with machine-readable reason,
request_id, validated submitted revisions/hash and optional details. Details after
work include coverage, execution, policy, usage and universe snapshot, but never
fabricated matches. Provider failures/budgets/deadlines preserve completed stocks
as a 200 partial response when possible. With zero evaluated stocks they are not
reported as a successful zero-match scan. Reason counts distinguish unsupported
metadata, invalid/stale data, cooldowns, unselected stocks and retrieval failures.

Terminal accounting partitions the frozen full universe:

`scanned + stale + skipped + failed + not_evaluated = universe_total`

Eligible separately counts validated ready data (including interrupted computation),
and is never smaller than scanned. evaluation_incomplete is a subset of not_evaluated.
HTTP attempts, documented credit-cost exposure, unknown-cost attempts and unknown
actual charged consumption are separate fields. Limits are run-local/new-path;
shared SQLite leases coordinate cooperating HTTP callers as documented in stage 6A.

The following are **illustrative response excerpts**, not production demo results:

```json
{
  "outcome": "evaluated",
  "revisions": {"pattern_revision": 7, "settings_revision": 3},
  "matches": [],
  "coverage": {"universe_total": 1, "scanned": 1, "eligible": 1,
    "stale": 0, "skipped": 0, "failed": 0, "not_evaluated": 0,
    "evaluation_incomplete": 0, "matches_returned": 0},
  "execution": {"partial": false, "stop_reasons": [], "provider_requests": 0,
    "hypotheses_evaluated": 11}
}
```

```json
{
  "outcome": "partial",
  "coverage": {"universe_total": 3, "scanned": 1, "eligible": 1,
    "stale": 0, "skipped": 0, "failed": 0, "not_evaluated": 2,
    "evaluation_incomplete": 0, "matches_returned": 0},
  "execution": {"partial": true, "stop_reasons": ["provider_budget"],
    "provider_requests": 1, "hypotheses_evaluated": 11},
  "provider_usage": {"http_attempts": 1, "documented_credit_cost": 1,
    "unknown_cost_attempts": 0, "actual_credits_consumed": null,
    "scope": "current_scan_only"}
}
```

```json
{
  "error": {
    "code": "CURRENT_SCAN_UNAVAILABLE",
    "message": "The current scan could not evaluate any stocks.",
    "reason": "publication_finalization_unknown",
    "request_id": "example-request-id",
    "revisions": {"pattern_revision": 7, "settings_revision": 3},
    "pattern_content_hash": "<submitted canonical content SHA-256>",
    "details": null
  }
}
```

Complete response schemas, including raw similarity, exact-derived completion,
freshness, pattern hash and universe/policy metadata, are published in OpenAPI.

## Migration backup protection

Before initialization can upgrade an existing pre-v4 SQLite database, the repository
calls backup_before_v4. SQLite's backup API runs against a pinned read snapshot and
includes committed WAL data. A backup integrity check must succeed first. Backup
failure aborts initialization before migration; copying only the main file is never
used. Migration DDL now runs in explicit transactions, with a locked version recheck
for cooperating initializers. Fresh databases and already-v4 databases do not need
a pre-v4 backup. Existing already-v4 data cannot retroactively acquire a pre-upgrade
backup. No non-test database was migrated or restored during this implementation.

Backups are placed beside the database:

`<database parent>/backups/<stem>.pre-v4.<UTC timestamp>.<unique id>.sqlite3`

Database, WAL, SHM and rollback-journal files are ignored by Git. Backups contain
application data and must remain private. The backup is local protection, not an
off-host disaster-recovery system.

Restoration procedure (operator action, not performed against user data):

1. Stop all application workers and other writers. Preserve the post-upgrade database
   separately with the SQLite backup API before changing configuration.
2. Open the selected pre-v4 backup as the backup API's source and a **new, unused**
   database path as destination; call source.backup(destination).
3. Run PRAGMA quick_check, inspect schema_migrations and verify expected data on that
   restored copy. Never overwrite an active main file or reuse its stale WAL/SHM.
4. Point the compatible application version's DATABASE_PATH at the restored copy,
   then restart. The older binary's newer-schema safeguard remains intact.

Disposable migration tests keep a WAL writer open, verify committed WAL content in
the pre-v4 backup, migrate the source, restore to a disposable new path, and confirm
backup failure prevents migration. No destructive experiment touches user data.

## Verification and remaining limitations

The known host event-loop issue still prevents async/TestClient execution here.
Tests are not weakened or relabelled passed. On a working environment, from backend:

```powershell
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -o addopts= tests/test_manual_pattern_scan.py tests/test_current_data.py
.\.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m ruff check src tests
.\.venv\Scripts\python.exe -B -m mypy --no-incremental src/shape_finder
```

The new integration module disables live AsyncHTTPTransport calls and injects
MockTransport. Cases cover cold/warm flow, stale refresh, full/forming filtering,
configured-policy labels, revision/hash propagation, coverage, budgets, failures,
cooldowns, moving retrieval clocks, deadline/disconnect and worker admission cleanup.

Before frontend integration/deployment, execute these blocked tests, verify actual
provider timing/aggregation and account policies, prepare trusted catalogue metadata,
profile deployment CPU/memory/cancellation latency, and establish off-host backups.
Split-adjusted historical revision limits and process-local candidate fairness from
stage 6A remain. No frontend, production deployment or production-readiness claim
is included in stage 6B.
