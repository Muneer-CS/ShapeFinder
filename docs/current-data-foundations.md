# Stage 6A: production data foundations

The internal services are available for stage 6B, but no public endpoint or UI has
been added. The current matcher, historical scanner/provider, and legacy cache
workflow retain their existing behaviour. No live market-data requests were made.

## Calendar dependency and integration

Adopted and pinned exchange-calendars 4.13.2 (Apache-2.0), with transitive packages
resolved in requirements-dev.lock. The pinned [official package metadata](https://github.com/gerrymanoim/exchange_calendars/blob/4.13.2/pyproject.toml)
supports Python >=3.10,<4 and NumPy >=1.26.4. Import, real schedules, and local tests
were checked on Python 3.12.14 / NumPy 2.3.5 / pandas 3.1.0. Existing dependency
versions were retained; new transitive pins include pandas, pyluach, toolz,
korean-lunar-calendar and python-dateutil. pandas emits upstream deprecation
warnings during calendar construction; these are visible, not suppressed.

`infrastructure/exchange_calendar.py:ListingCalendar` implements the foundation
calendar interface. It requires trusted active US common-stock listing metadata.
Only NYSE/XNYS and NASDAQ/XNAS listing exchange fields map to calendars. The pinned
[upstream mappings](https://github.com/gerrymanoim/exchange_calendars/blob/4.13.2/exchange_calendars/calendar_utils.py)
alias NASDAQ and XNAS to XNYS. This is upstream's shared regular-session calendar,
not independently reconstructed NASDAQ holiday rules. Unknown exchanges/countries
are unavailable; ticker spelling is never a mapping source.

Each instance has an explicit start/end operational window within 2000-01-01 through
2030-12-31. These are application bounds, not a claim that the package cannot
generate other dates. The adapter reports the exact window and package version;
outside it, eligibility fails closed. Future holiday-rule changes and extraordinary
closures require maintained package/calendar updates. No calendar predicts future
unscheduled closures. A request window producing over 5,000 candles is rejected;
6B should choose a narrow interval-appropriate window covering the required history.

Real schedule tests cover July 4 closure, July 3 early close, both DST transitions,
and shortened final hourly buckets. Session-open buckets are calendar policy only:
intraday OHLC cannot use them until provider alignment evidence is supplied.

## Provider evidence and capability statuses

[Official time-series documentation](https://twelvedata.com/docs/market-data/time-series)
establishes bar-open timestamp meaning, intraday timezone controls, exchange-local
daily dates (daily timezone override is ignored), the interval names, outputsize
1..5000, regular/prepost controls, one documented credit per symbol for time_series,
and adjustment modes with splits as the default. The
[timezone guide](https://support.twelvedata.com/en/articles/5745849-timezones)
also describes equity exchange-local defaults. These documents do not establish
this account's delay, finalization SLA, all intraday bucket boundaries, historical
entitlements, or snapshot event time. No account-specific capability was tested.

| Capability | Status in this implementation |
| --- | --- |
| NYSE/NASDAQ regular calendar and identities | Usable within explicit adapter windows |
| Daily session-date conversion | Usable for supported exchange timezone/date semantics |
| Intraday timestamp meaning / UTC parsing | Documented; exact grid matching implemented |
| Intraday session-open aggregation | Unavailable by default; requires explicit confirmation/provenance |
| Publication and finalization timing | Unknown by default; current-readiness planning unavailable |
| Operator-supplied timing | Configured-but-unverified unless explicitly attested as verified |
| Unfinished snapshots | Unavailable; no enabling configuration here |
| Account rate/quota entitlements | Unknown unless explicitly configured with provenance |

`TimingPolicy` validates interval, nonnegative publication/finalization delays,
finalization ordering, review timestamp and provenance. Intraday bucket confirmation
requires separate provenance. `basis=verified` is a trusted operator attestation
that must cite actual evidence, not automatic verification performed by this code.
No real verified policy ships. A configured policy is labelled as such and planning
requires explicit acceptance; merely loading configuration does not promote it.

Settings expose CURRENT_TIMING_POLICIES (default []), CURRENT_DISPATCH_LIMITS,
CURRENT_ACCOUNT_POLICY (default null), and CURRENT_ACCOUNT_SCOPE. Nested settings
use JSON. Do not enter speculative delays or label assumptions verified. The scope
is a non-secret stable coordination label shared by all new-path clients using
the same provider account. No API key is included in reports, SQL logs or policy hashes.

## Canonical conversion and readiness

`application/current_history.py` plans expected observations using the real calendar
and a frozen cutoff. Unknown capability, unaccepted configured policy, provisional
latest candle or insufficient calendar horizon raises DataUnavailable with a stable
reason. The last cache timestamp does not choose the endpoint.

Daily source dates must be exchange-local midnight dates; they map to real session
open/close instants. Intraday observations must match confirmed candle opens exactly.
Unexpected times, duplicate sessions, unordered data and inconsistent identity/OHLC
are rejected. There is no OHLC rebucketing. The new provider checks original response
ordering before the legacy parser's sorting could hide malformed input. The legacy
parser itself is untouched.

`CurrentHistoryDataService` reads canonical cache first and verifies all required
identities, endpoint, policy/calendar fingerprint, and retrieval after the stated
finalization boundary. Missing observations trigger only controlled foreground
fetches. An explicit calendar horizon supplies the full required maximum duration;
6B must not present a truncated response as a shorter listing history.

Requests use at most 240 required observations per chunk, explicit outputsize=5000,
order=ASC, prepost=false and adjust=splits. The last two preserve the documented
regular/default adjustment policy. No speculative provider pagination is used.
Every returned observation is validated against the requested calendar sequence.
Missing/truncated responses remain incomplete; there is no blanket requested-range
coverage write. Valid observations from such responses can be retained for retry.
Each validated chunk commits separately so later failure cannot erase earlier chunks.

Split-adjusted historical prices can be revised. Finalization timing is not proof
of an immutable historical vendor snapshot, corporate-action consistency across
different fetches, or point-in-time backtest data. 6B must retain this limitation and
define refresh/invalidation on known corporate actions; no such feed is integrated.

## Persistence and migration

Migration 4 adds current_candles, provider_coordination, provider_attempts and an
attempt-time index. Existing market_bars, coverage, catalogue and failure tables
are preserved. New canonical rows store individual identity/OHLCV, retrieval time,
calendar version and policy fingerprint. Old fetch/coverage metadata is never
promoted to proof; daily legacy midnight timestamps remain untouched. A cache miss
requires revalidation/refetch through the controlled new path.

The migration is additive and old data survives upgrade; an older binary's existing
newer-schema safeguard will refuse schema 4. Rollback therefore needs a pre-upgrade
database backup, not automatic table deletion. No production database was migrated.

## Dispatch guarantees and assumptions

`SharedDispatcher` creates bounded gateways; every new-path time_series or stocks
attempt crosses `_DispatchTransport`, including provider retries and chunk calls.
No raw provider/client is exposed to new hydration/catalogue consumers. Unsupported
targets, endpoints, multi-symbol requests and interval mismatches are refused.
There is no background refresh or hydration.

Default server limits per DispatchRun are six daily or three intraday admitted HTTP
attempts, five daily or two intraday distinct requested symbols, and 20 seconds.
Limits are validated/configurable. Retries spend attempts but not extra symbol slots;
catalogue refreshes spend attempts and record unknown credit cost. Cache hits spend
none. An admitted attempt may be cancelled before transmission, so accounting can
conservatively overcount wire attempts, never grant extra attempts.

SQLite BEGIN IMMEDIATE reserves one account-scoped HTTP lease across all cooperating
workers sharing the same database. The short transaction commits BEFORE HTTP and
the lease remains through the response body. Concurrent admission fails explicitly
as provider_busy rather than creating an unbounded queue. Leases expire by the run
deadline; response/cancellation cleanup releases them. DB contention waits on the
new store are bounded to 100ms. HTTP cancellation requires cooperative transport
behaviour (the production httpx transport), not a transport that ignores cancellation.

Overall gateway/service deadlines also cover retry sleep and chunk loops. Cancellation
events interrupt active HTTP; external task cancellation propagates. Safe completed
DB writes can finish during cancellation cleanup without fabricating full coverage.
401/403/429 persist a cooldown at least the configured local 60-second backoff or a
larger valid Retry-After value. That fallback is local throttling, not a verified
account reset time. Existing symbol failure cooldowns are respected after cache
misses; unsupported symbols/no-data record the existing local seven-day policy.

Optional account policies enforce rolling 60-second admitted-attempt and rolling
86400-second documented-cost bounds in the shared ledger. Unknown cost blocks
credit-limited admission instead of being treated as zero. Known time_series cost
is documented cost exposure, including retries; actual charged consumption remains
unknown without account reconciliation. These policies are administrator assertions,
not evidence of account entitlement. The ledger stores no credentials or query URLs.

Deployment assumptions: the current Docker command starts one uvicorn worker. The
new coordination also works across cooperating workers on the same SQLite file and
system clock. It is not distributed coordination for replicas with separate volumes.
Legacy MarketDataService/UniverseService still have their compatible direct-provider
paths. They must NOT be reused unchanged for a new current-scan endpoint; new-path
hydration/catalogue calls must use its run-bound gateway. Legacy traffic or other
applications sharing the API key is not included in these new-path guarantees, so
the dispatcher cannot guarantee total account consumption. Stage 6B must preserve
that distinction or deliberately integrate all account traffic before claiming it.

## Verification and remaining integration

Synchronous tests exercise real calendar schedules, canonical mapping, readiness,
configured/unknown exclusions, migration from schema 3, atomic chunk persistence,
legacy non-promotion, shared lease/cooldown/rate/credit policy, and local run guards.
Mocked async tests cover retry/chunk accounting, cache reuse, response truncation,
later chunk failure, active cancellation, deadline cleanup and symbol cooldowns.
They are authored but unexecuted here because of the known backend event-loop
blocker. No runtime workaround or repeated probe was attempted.

6B must construct one run per manual scan, initialize/use the shared store and
dispatcher, resolve listing/calendar/capability reports, account for the frozen full
universe, connect ready histories to the matcher, propagate cancellation, and expose
truthful capability/coverage results. It must establish timing/account evidence
before enabling unavailable capabilities and validate the async tests in a functioning
runtime. This stage does not claim the scanner is production-ready.
