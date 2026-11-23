# Current-pattern foundation

This stage adds contracts and a pure eligibility policy only. No new route, scan
orchestration, scoring, editor, interpretation, calendar installation, provider
request, SQLite migration, or deployment is included. The historical scanner and
four-component scorer remain unchanged.

## Wire contract

`api/current_scan_schemas.py` reuses the frozen validated models in
`core/pattern_scan.py`. All wire keys use snake_case. Unknown fields are rejected.
The future request shape is:

```json
{
  "pattern": {
    "schema_version": 1,
    "points": [{"x": 0, "y": 0}, {"x": 0.4, "y": -1}, {"x": 1, "y": 0.6}],
    "provenance": {"kind": "drawing"}
  },
  "revisions": {"pattern_revision": 1, "settings_revision": 1},
  "settings": {
    "interval": "1day",
    "duration": {"minimum_candles": 20, "maximum_candles": 120},
    "minimum_completion": 0.5,
    "minimum_movement_percent": 5,
    "minimum_similarity": 80,
    "match_type": "all",
    "session": "regular",
    "include_unfinished": false,
    "top_n": 20
  },
  "universe": {"kind": "nasdaq"}
}
```

Similarity is a user-configurable score on 0–100, default 80. Zero explicitly
disables its threshold; null is invalid. Completion uses a fraction on (0,1],
not percent units. Movement uses percent units. Defaults are provisional research
settings, not validated trading thresholds. Five percent can be restrictive for
short intraday sequences; broad duration/prefix searches multiply computation.

Canonical points have strictly increasing x in [0,1], with exact endpoints 0 and
1. There are 2–256 finite points. y is unitless, upward-increasing shape in
[-1000,1000], not dollars or a movement threshold. Interpolation is piecewise
linear. Identical y values are flat and rejected. There is no absolute epsilon
that would reject a small but representably nonconstant drawing. Positive affine
changes remaining within coordinate bounds preserve shape eligibility; changes
smaller than binary64 representability cannot be preserved. Prefix flatness and
numerical normalization belong to the next matcher stage, using only the clipped
prefix. No prefix interpolation or scoring is performed here.

The next matcher must normalize each clipped prefix independently before converting
it to positive pseudo-prices. Drawing translation/height must not feed the legacy
amplitude component. `current_shape_v1` reserves shape/direction/error scores with
amplitude null; it is only a contract identifier here. Stock movement remains
100 * (max observed close / min observed close - 1), independent of shape units.

Stock provenance requires source_symbol, source_interval, source_start_at and
source_end_at (aware, ordered timestamps). Text provenance requires grammar_version
1. Screenshot bytes and arbitrary source text are not part of this contract.

## Content hash and acknowledgement

`CanonicalPattern.content_hash()` is SHA-256 over compact, sorted-key JSON, encoded
as UTF-8, with ASCII escaping. Include schema_version, all points, and every
provenance field (including explicit null defaults from model serialization).
Encode each point number as 16 lowercase hex digits representing big-endian IEEE-754
binary64 bytes; normalize negative zero to positive zero. This avoids differences
between Python and JavaScript floating-point decimal rendering. Future JS code
must reproduce these bytes and JSON escaping, with cross-language golden tests.
Hashing is content identity, not shape equivalence: changing drawing scale or
provenance changes its hash even when similarity would be invariant.

Text/screenshot requests require preview_acknowledgement containing
pattern_revision and content_hash. Both must match the submitted canonical pattern
and revisions. Optional acknowledgements for other methods are checked equally.
Edits invalidate the acknowledgement; even reaccepting identical content at a new
revision requires the new revision number. Settings edits increment settings_revision
and mark results previous, but do not invalidate acceptance of unchanged pattern
content. These stateless contracts check internal consistency, not monotonically
increasing revisions across requests. No approval service or security claim exists.

## Results and coverage

Completion is computed as the exact rational (observed_candles - 1) /
(complete_duration_candles - 1); floats are output measurements only. Counts must
satisfy 8 <= observed <= complete <= 240. For D=21, N=11 is exactly 50%; N=10 is
45%. For D=20, N=11 is 52.63%, not 50%. Full means N=D. A forming-only setting
must admit at least one achievable forming alignment. Results cannot submit a
contradictory completion or match_type; these are computed response-only fields.

Result validation checks unique stocks, top_n, thresholds, duration, completion,
match type, timeframe, cutoff and unfinished settings. Score calculation and
filter-before-deduplication are deferred to the matcher.

Terminal coverage partitions the frozen universe:

`scanned + stale + skipped + failed + not_evaluated = universe_total`

`matches_returned <= scanned <= eligible <= universe_total`

Eligible is overlapping, not another terminal category. evaluation_incomplete is
a subset of not_evaluated. A corrected example is total 1000, scanned 250, eligible
250, stale 10, skipped 20, failed 5, not_evaluated 715, returned 1. Partial coverage
must be marked partial. Stopped execution includes explicit reasons and actual
provider-attempt/hypothesis counts; budget enforcement is future orchestration work.

## Eligibility boundary

`FrozenCutoff.freeze(clock)` samples an injectable clock once. The pure resolver
does not read a clock or fetch data. Its calendar adapter supplies explicit exchange
session boundaries and supported date range, including holidays, DST and early
closes. Fixtures are synthetic schedule examples, not a production exchange calendar.

Only a verified session-open bucket policy is implemented. Daily identity is the
session's open/close and exchange-local session date, never UTC midnight. Intraday
buckets start at session open and the last may be shortened at session close.
This is a supported policy conditional on verified provider capability, not a claim
about Twelve Data aggregation. Regular sessions only are supported.

The resolver selects the expected latest identity independently of cache contents.
Known publication and finalization delays are required; unknown values are never
substituted with zero. A publishable but not yet finalized latest candle excludes
the candidate rather than falling back to an older candle. A finalized cache entry
must have been retrieved at or after its verified finalization boundary. Later
retrieval of a completed candle is allowed, but does not promise historical
point-in-time vendor data or immunity to later corrections.

Unfinished mode requires independently verified snapshot capability and a known
maximum snapshot age. Snapshot time must be inside the current candle and no later
than cutoff/retrieval. After hours, verified opt-in can resolve a completed bar.
Unknown capability, calendar range, missing/stale identity, provisional data and
invalid/stale snapshots have explicit exclusion reasons. No application startup
or endpoint currently invokes the new resolver.

`UnverifiedProviderCapabilities` deliberately makes every interval unavailable.
No current database column proves finalization/snapshot provenance, so existing
SQLite rows cannot simply be declared current through this new policy. Existing
cache/database behaviour is untouched; a verified persistence adapter belongs to
later integration. No speculative migrations were added.

## Official documentation checked, without API requests

- [Twelve Data time series](https://twelvedata.com/docs/market-data/time-series):
  documents supported interval names, intraday timezone control, exchange-local
  daily dates, bar-open timestamp semantics and pre/post controls. This does not verify this account's delay,
  finalization SLA, bucket alignment or timestamped unfinished snapshots.
- [Twelve Data timezone guide](https://support.twelvedata.com/en/articles/5745849-timezones)
  describes exchange-local equity defaults and timezone overrides.
- [exchange_calendars upstream](https://github.com/gerrymanoim/exchange_calendars)
  and its [package metadata](https://github.com/gerrymanoim/exchange_calendars/blob/master/pyproject.toml)
  identify an Apache-2.0 candidate with Python 3.12 support. Neither it nor
  pandas_market_calendars is installed here. No dependency was selected/installed;
  pinned-version supported dates, exchange mappings and compatibility with this
  repository remain unverified. This stage uses the adapter/fixture fallback only.

## Verification boundaries

App workflow tests preload the real ChartVisuals module before assertions.
App.lazy.test.tsx separately gates that module import, checks the loading fallback,
then explicitly resolves and awaits the real module and checks the actual chart.
It does not extend timeout values or mock away rendering. A process-local shared
TEMP/TMP may still be required by this sandbox; no machine path is in configuration.

The Windows asyncio event-loop probe remains blocked in local socket-pair creation.
Do not change provider/application logic to compensate. Synchronous tests can run
offline, but async/TestClient tests and the full coverage gate must remain reported
as unverified until the runtime is repaired.
