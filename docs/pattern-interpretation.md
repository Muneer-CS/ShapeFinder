# Stage 8: local descriptions and screenshot tracing

The Draw, Describe, Upload and Stock chart choices share the existing canonical
`Pattern` and `PatternEditor`. No interpretation service, new backend endpoint,
dependency, provider request or deployment configuration was added.

## Supported text grammar (version 1)

`pattern/textPattern.ts` accepts these complete, comma-separated forms:

- `up 3, down 1, up 2` produces `(0,0), (0.5,1), (2/3,0), (1,1)`.
- `flat 1, down 2, up 2` produces `(0,0), (0.2,0), (0.6,-1), (1,0)`.
- `points: 0:0, 0.4:-1, 1:0.6` uses exactly those three coordinates.

Case, whitespace, finite decimal/scientific numbers and optional number signs are
supported. Segment numbers mean relative horizontal duration. Each up/down changes
height by one unit; flat leaves height unchanged. These numbers are neither price
percentages nor candle counts. The parser does not understand arbitrary prose or
named market patterns. It rejects every unrecognized/empty fragment, nonpositive
or nonfinite duration, overflowing totals and unrepresentable point ordering.
Shared canonical validation enforces endpoints, finite coordinate bounds, nonflat
geometry and 2 to 256 points. Text is not silently simplified. There are at most
255 segments and 20,000 source characters.

Generate preview creates a separate canonical draft with text provenance and
grammar version 1. Users can edit its shared control points before Accept preview.
Source text never enters the scan request; only geometry and canonical provenance
are submitted.

## Local image handling and coordinate mapping

`pattern/localImage.ts` accepts static PNG, JPEG and WebP. It checks MIME type and
binary signatures, rejects animated PNG/WebP containers, caps input at 10 MiB, and
checks decoded dimensions (32 to 8192 per side, at most 24 million pixels).
Dimension checks occur after the browser reads the image; this is not a guarantee
against all decoding memory costs. No pixels are extracted or analyzed. Image
bytes, filenames, metadata, object URLs and crop settings are never sent to the API
or external services. Browser-local blob URLs are revoked on failure, cancellation,
successful replacement or unmount; cancelled reads/decodes cannot publish late.

`ScreenshotInput.tsx` provides a rectangle overview plus keyboard-accessible region
percentages. Percentages are measured from the original image's top-left corner.
The selected region must be ordered and at least 1% wide/high. Apply chart region
changes the interpretation bounds deliberately; Create tracing preview starts an
empty, separate screenshot-provenance draft. Existing applied geometry stays intact.

The same editor draws the cropped image and points through one SVG transform:

- Selected left/right map to canonical x=0/1.
- Selected top/bottom map to canonical y=1/-1.
- A point inside the crop maps affinely, with height increasing upward.

Trace left to right across both boundaries at zoom 1. Endpoint positions snap within
four screen pixels; an incomplete-width or backwards stroke is explicitly rejected,
not stretched into another image region. Existing simplification and point limits
apply. Numeric/keyboard edits and gesture undo/redo use the same editor as drawings.
Tracing y edits stay within the selected region. View-only zoom/pan preserve
canonical coordinates and acceptance; zoomed views support inspection and point
editing, with buttons for horizontal panning on narrow screens. Source/method/zoom
changes discard in-progress editor gestures; resizing or panning also cancels an
unfinished gesture. The image and trace remain aligned under the shared transform.

Changing region controls invalidates the previous interpretation immediately.
Applying a new crop never remaps accepted points or places them against a different
image silently. A stale image overlay is hidden, geometry is retained, and scanning
is blocked until a fresh trace is reviewed and accepted. Failed/cancelled image
replacement preserves the previous image and applied pattern. Successful replacement
requires a new interpretation for an existing screenshot-derived pattern.

## Applied patterns, drafts and acknowledgement

`workspace/usePatternInput.ts` owns separate text/screenshot draft histories and
the applied canonical pattern/history. Changing methods preserves drafts and the
applied provenance. Generate/Create preview does not apply a replacement. Cancel
preview returns to the applied geometry; source edits are retained and still require
a fresh interpretation if the applied pattern came from that source.

Accept preview validates and hashes the visible content using the existing Python-
compatible SHA-256 algorithm, then applies a replacement at a new monotonic pattern
revision. The acknowledgement contains that exact revision/hash. Editing applied
geometry, changing provenance, undo/redo, or editing its source invalidates acceptance.
Even undoing to identical content needs acceptance at its new revision. Settings-only
edits preserve acceptance and mark prior results as previous settings. Switching to
Draw does not relabel existing text/trace geometry or create acceptance.

An epoch guards asynchronous acceptance hashing; unique preview identities and
source versions prevent an older completion from applying or acknowledging a newer
draft. Scan AbortController/request guards remain in place across source/method
changes. The client checks acknowledgement against the request hash again before
POST. Neither generation, image loading, editing nor acceptance triggers scans,
catalogue refresh, hydration or price-history loading. Only explicit stock-history
loading and manual scans can use market data. Capabilities remain authoritative;
valid/accepted previews cannot enable an unavailable scan timeframe.

## Verification and remaining scope

Run `npm run check` from `frontend` for lint, formatting, offline Vitest tests,
TypeScript and production build. Stage 8 adds parser boundary examples, local image
resource/format limits, exact crop/trace coordinates, preview replacement/cancellation,
source invalidation, keyboard/numeric edits, settings-only acceptance, stale hashes
and scan replies, and no-upload/no-auto-request checks. Test Image/File gaps in JSDOM
are explicitly mocked; browser verification independently exercises actual PNG loading,
pointer tracing, image alignment, errors and desktop/narrow layouts using a mock API.
Fixtures and screenshots live outside the project and are not production fallbacks.

Offline calls to the actual Python CanonicalPattern model established golden hashes
for text and screenshot provenance; no backend files or endpoints changed. The user-
verified backend baseline remains 326 passed and 94.48% coverage. Default current-scan
timing policies/provider entitlements remain unverified and unavailable as documented
in stage 6B; local interpretation does not change them.

No general natural-language AI, OCR, automated screenshot extraction, price/timestamp
inference or named-pattern recognition is claimed. Stage 9 still owns saved themes,
About, final visual polish and detailed result overlays.
