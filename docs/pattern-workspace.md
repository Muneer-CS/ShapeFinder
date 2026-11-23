# Stage 7: editable current-pattern workspace

The production entry point renders `workspace/PatternWorkspace.tsx`. The historical
frontend remains in `LegacyApp.tsx` for its existing offline regression suite, but
is not imported into the production bundle or exposed in navigation. Historical
backend routes and scoring remain unchanged.

## Editor and source import

`pattern/model.ts` owns canonical point validation, drawing coordinates,
simplification, log-relative stock-section import and content hashing.
`PatternEditor.tsx` supports mouse/touch pointer capture, point dragging, arrow-key
adjustments, numeric coordinates, midpoint insertion and interior-point deletion.
Undo/redo records one completed gesture or explicit editing action. Reset returns
to the last completed drawing/import; Clear empties the editor and can be undone.

Strokes must progress left to right. Any backwards movement discards the stroke
with an explanation. Repeated x samples retain the latest height, explicitly
described in the editor. Zero-width strokes are rejected; pointer cancellation or
lost capture keeps the committed pattern. The stroke's horizontal extent maps to
exact endpoints 0 and 1. Upward screen motion increases canonical y. View resizing
never rewrites canonical points. Dragging is bounded by neighbours; invalid numeric
edits are rejected rather than silently clamped. Endpoint x is fixed.

Dense geometry is reduced to at most 256 points by retaining endpoints and the
largest vertical deviations first. This is an approximation, not a guarantee that
every turn survives. The sample/observation count and resulting control-point count
are shown, and the actual submitted geometry is visible and editable.

`StockSectionImport.tsx` reuses `getMarketData` only after Load stock history.
Date bounds cover full UTC days. Chart clicks select a start then an end observation;
labelled selects provide the keyboard alternative. Invalid/unordered/flat sections
cannot be imported. Height is `log(close / first close)` and x is equally spaced
by observation, irrespective of market closures. Source symbol, interval and range
are retained through edits. UTC timestamp serialization preserves provider
microseconds and matches Pydantic's canonical fractional formatting.

## API and state

Only the side-effect-free capabilities endpoint is read automatically. No startup
history load, catalogue refresh, hydration or scan is scheduled. Missing/failed
capabilities disable scanning while preserving editing. Timing basis and unfinished
candle restrictions come from the server; the client invents no policy.

Settings mirror the backend defaults: daily candles, US equities, similarity 80,
completion 50%, complete duration 20 to 120 candles, movement 5%, All, regular
session, completed candles and top 20. Completion converts UI percent to the wire
fraction; explicit zero similarity/movement remains zero. Custom symbols normalize
and deduplicate to at most ten supported tickers. Duration and forming feasibility
are validated before submission. Complete duration describes the whole pattern,
not the number of observations in its forming prefix.

Hashing reproduces the existing binary64, negative-zero-normalized, sorted compact
ASCII JSON SHA-256 contract, including nullable provenance fields. Frontend tests
use the backend's drawing regression vector plus an imported-provenance vector
computed offline by the actual Python model. No validation endpoint was added.

Pattern and settings revisions increase on committed changes, including undo/redo.
Old results remain explicitly previous. A synchronous ref guards submission before
async hashing. AbortController and unique request identity guard scan/history loads,
edits, cancellation, unmount and late replies. Response revisions and pattern hash
must match the submitted request. Malformed and duplicate-stock responses fail
instead of rendering a misleading table. Backend cancellation remains cooperative;
aborting the browser does not promise immediate termination of server work.

Results separate successful no-match, partial coverage, validation, capacity,
unavailable and failed/deadline outcomes. Rows use server similarity, completion,
full/forming type, movement, observation/duration counts and explicit candle and
retrieval timestamps. Cutoff, coverage, stale membership and timing basis remain
visible. There is no forecast probability or old truncated comparison overlay.

## Verification and boundaries

From `frontend`, run `npm run check` (lint, formatting, offline Vitest suite,
TypeScript and production Vite build). No dependency installation or live provider
is required. This Windows sandbox may need process-local TEMP/TMP pointing to a
shared writable directory, as established in the earlier test-baseline investigation;
no machine path is embedded in project configuration.

Stage 7 tests mock every request. They cover canonical compatibility, geometry,
numeric/keyboard edits, gesture undo, resize stability, imports, settings, capability
failure, manual-only scans, duplicate prevention, aborted/late requests, response
identity, invalid replies, coverage and one row per stock. Browser verification uses
a separate local mock server outside this repository. JSDOM assertions are not
visual verification or evidence of account entitlement/provider freshness.

The user independently verified the pre-stage-7 full backend gate: 326 passed,
94.48% coverage, with RuntimeWarning and PytestUnraisableExceptionWarning as errors.
Stage 7 changes no backend files or endpoints. Default current-scan timing policies
remain unavailable until evidence-backed server configuration exists. Scanning also
requires cached listing metadata, adequate validated prices and provider access
when hydration is needed. This stage neither verifies nor changes those conditions.

Stage 8 owns text interpretation and screenshot tracing with editable approved
previews. Stage 9 owns final polish, complete results/overlays, saved themes and
About. No placeholders for those features are exposed here.
