# P1 Batch Contracts

**Status:** Milestone P1.0 contract through P1.6 progress and results interface

**Schema proposal version:** 2

**Last updated:** 2026-08-12

## Milestone boundary

P1.0 defines provider-neutral domain contracts, deterministic blank templates, API behavior,
export safety, and an additive database proposal. P1.1 implements bounded workbook preflight, and
P1.2 applies the additive migration and persists short-lived drafts. P1.3 exposes the template,
preflight, retrieval, correction, and replacement routes and the reviewer-facing preflight UI.
P1.4 adds durable idempotent start and independently processes selected cases. P1.5 completes the
bounded polling, case-detail, outcome-summary, and safe CSV download APIs. P1.6 adds the dedicated
batch page, progress polling, triage filters, reusable P0 detail, refresh recovery, and terminal
download interface.

No module under `app/batches` may import the OpenAI SDK. Each selected case will eventually call
the existing P0 review boundary independently; expected values, filenames, spreadsheet content,
other cases, and prior results never enter the extraction request.

## Spreadsheet contract

The first row of the `Batch` worksheet or CSV must contain these exact, case-sensitive headers in
this order:

1. `Application ID`
2. `Label Image Filename`
3. `Expected Brand`
4. `Expected Class/Type`
5. `Expected ABV`
6. `Expected Net Contents`

There are no header aliases. Leading or trailing header whitespace is invalid rather than silently
corrected. Blank rows do not count as cases; no more than 25 nonblank data rows are accepted.

`Expected Net Contents` remains one human-readable cell because that is the accepted product
contract. It accepts one positive metric quantity such as `750 mL` or `0.75 L`. `Expected ABV`
accepts a decimal percentage from 0 through 100, with an optional trailing percent sign. The parser
will reject formulas and external links instead of calculating or following them.

Application IDs and filenames are trimmed and Unicode-normalized to NFC for matching. Duplicate
application IDs are detected case-insensitively. A label reference must be a base filename with no
directory or drive component. Filename matching is case-insensitive after NFC normalization;
multiple original filenames that resolve to the same normalized key are ambiguous and rejected.

## Accepted limits

| Limit | Value |
| --- | ---: |
| Cases | 25 nonblank data rows |
| Initially selected images | 25 |
| Spreadsheet file | 1 MiB |
| XLSX archive entries | 200 |
| XLSX uncompressed content | 10 MiB |
| Spreadsheet source-row span | 250 rows, including blank rows |
| Spreadsheet and images combined | 100 MiB |
| Any populated spreadsheet cell | 500 Unicode code points |
| Application ID | 100 Unicode code points |
| Image base filename | 255 Unicode code points |
| Expected brand or class/type | 200 Unicode code points |
| ABV cell | 32 Unicode code points |
| Net-contents cell | 64 Unicode code points |
| Image | Existing P0 limit: 10 MiB, 40 megapixels, 6,000 pixels per side |
| Polling representation | 256 KiB maximum encoded response |
| Poll interval while active | 1.5 seconds |
| Absolute content retention | 24 hours from draft creation; polling and edits do not extend it |
| Graceful-shutdown drain | 15 seconds |

The 100 MiB aggregate cap is independent of the 10 MiB per-image cap. A 25-case package remains a
valid product shape, but its images may need to be compressed to fit the bounded request envelope.

## P1.1 parsing and association policy

The P1.1 parser makes the following concrete choices within the P1 contract:

- File suffix selects the spreadsheet parser. `.csv` and `.xlsx` are accepted case-insensitively;
  `.xlsm` is rejected explicitly and other suffixes are unsupported. Content is still validated and
  browser MIME types are not trusted.
- CSV accepts strict UTF-8 with or without a UTF-8 byte-order mark. NUL bytes, invalid encoding, and
  malformed quoting are rejected.
- XLSX parsing reads only the exact `Batch` worksheet. Additional worksheets, including the
  generated `Instructions` sheet, are ignored. A missing `Batch` sheet is an error.
- XLSX ZIP metadata is inspected before OpenPyXL loads the workbook. Encrypted entries, compound
  encrypted/binary files, VBA content, external-link parts, more than 200 archive entries, or more
  than 10 MiB of uncompressed content are rejected.
- A spreadsheet may span at most 250 source rows, including blank rows. This complements the
  25-nonblank-case limit and prevents a sparse XLSX dimension from causing unbounded iteration.
- Actual XLSX formula cells are rejected. The parser uses `data_only=False` and never substitutes a
  cached formula result. CSV cells are always parsed as data because CSV has no formula type.
- Text is trimmed and normalized to NFC. Numeric XLSX cells are converted to stable display text;
  this allows numeric application IDs and expected numeric fields without evaluating formulas.
- Overlong values retain only the documented bounded prefix and receive an error. Uploaded content
  beyond a field's response/storage limit is never echoed or carried into a draft contract.
- Image association is one-to-one. Reusing one normalized image filename in multiple spreadsheet
  rows is a duplicate-reference error, because processed images are deleted per case later in P1.
- Every structurally admissible selected image passes through the existing byte-signature, decode,
  dimension, animation, orientation, and metadata-stripping boundary. Filename and MIME claims do
  not decide image type.
- A valid image may be prepared for a row whose expected values still need correction, but the case
  remains `needs_correction` and is not eligible for extraction.
- A structurally invalid spreadsheet is deleted immediately after parsing and prevents image decode.
  Raw image spools are then deleted on context exit. This avoids expensive image work for a package
  that cannot become a draft.
- Unreferenced valid images are validated and reported as warnings. Missing, duplicate, ambiguous,
  or invalid associations are never chosen automatically.

The preflight preparation result is an async context manager. Normalized image paths are valid only
inside that context; P1.2 copies associated images to protected draft storage before exit. Raw
spreadsheet and image-spool files are never part of the returned contract.

## P1.2 draft persistence policy

P1.2 makes the following concrete lifecycle and storage choices:

- Batch, case, and image identifiers are UUIDv4 values. Files use a separate random 128-bit storage
  key, so neither an identifier nor an uploaded filename determines a filesystem path.
- A draft is created only from a structurally valid preflight containing at least one parsed case.
  Row-level correction issues are persisted; a whole-package structural error is not persisted.
- Only valid images associated with a case are copied from preflight into draft storage. Invalid,
  ambiguous, duplicate, and unreferenced images remain preflight-owned and are deleted on context
  exit.
- The image directory is mode `0700` and normalized image files are mode `0600`. Stored files remain
  metadata-stripped PNGs produced by the existing byte-based intake boundary.
- Cases retain the bounded spreadsheet image filename even when no image was associated. Their
  normalized application ID and normalized image filename are nullable so invalid rows can be
  recovered and corrected after refresh.
- Expected-value correction revalidates only the selected case and does not rewrite another case or
  its image reference. Invalid corrections remain bounded `needs_correction` drafts.
- Image replacement is an explicit case association, so it does not use batch-wide filename
  matching. A valid replacement may share its display filename with another case without ambiguity;
  the case UUID determines the target. A rejected replacement leaves the prior image unchanged.
- Replacement stores the new image before the database swap, commits the case association, and then
  removes the old file. A failed old-file deletion becomes an orphan for the lifecycle cleanup to
  retry.
- Expiry is exactly 24 hours from draft creation and is never extended by retrieval, correction, or
  replacement. Unknown, expired, cross-batch, and non-draft mutation targets share one internal
  not-found result.
- Startup cleanup removes expired database content and unreferenced files before the app accepts
  work. The periodic task wakes at least every five minutes and sooner for the next known expiry.
  It scans only direct children of the private image directory.
- The draft service intentionally has no collection/list operation, and P1.2 adds no HTTP routes.
  Service operations emit no content, filename, identifier, or source-address logs.

## Preflight issues

`PreflightIssueCode` is the stable machine contract. Its `message` and `severity` are computed from
the code so uploaded or model-derived text cannot become an accidental public error message.
Issues may be batch-, row-, or image-scoped; row issues include the original spreadsheet row
number. An unreferenced selected image is a warning. Every other defined issue is an error and
prevents the affected case, or the whole structurally invalid package, from becoming ready.

Invalid expected values are retained as bounded strings for correction, while
`normalized_expected` exists only after all expected values validate into the existing
`ExpectedReview` model. Invalid cases never receive provider work.

## P1.3 API and interface policy

P1.3 makes the following concrete interaction choices:

- The batch workflow is a clearly labeled page at `/batch` beside the single-label page at `/`. It uses
  separate native file controls for one spreadsheet and multiple images and never asks for a ZIP.
- Template downloads use stable `label-review-batch.xlsx` and `label-review-batch.csv` attachment
  names. The interface displays the 25-case, 25-image, 1 MiB spreadsheet, 10 MiB per-image, and
  100 MiB package limits before selection.
- A structurally valid package returns `201 Created`, a `Location` header, and a durable draft even
  if every row needs correction. A structurally invalid package returns `413` or `422` with bounded
  `PreflightIssue` values and no draft identifier.
- Selecting no images is accepted at the HTTP boundary. This supports spreadsheet-first preflight;
  affected rows receive `missing_image` and can use case-specific replacement afterward.
- Batch summaries remain small. The UI requests case detail only when expected-value editing begins,
  then patches the four expected fields through the existing bounded correction contract.
- Image replacement targets a case UUID directly and updates the displayed filename. The browser
  uses the same JPEG, PNG, and WebP restrictions as the server, while server byte validation remains
  authoritative.
- The browser stores the draft identifier only in the current URL query string. Refresh recovers the
  server draft from that identifier; content and filenames are not copied into local storage.
- Each row displays textual `Ready` or `Needs correction` status plus a symbol and plain-language
  issues. Readiness is not communicated by color alone.
- `Process all ready cases` is disabled when no row is ready and always opens a keyboard-focused
  confirmation. When corrections remain, the dialog states their count and that they will not be
  selected.
- The P1.3 confirmation boundary is retained. P1.4 activates it by posting either `all_cases` or the
  explicitly disclosed `ready_cases_only` selection with one browser-generated idempotency key.
- Unknown, malformed, expired, and cross-batch identifiers return the same bounded `batch_not_found`
  representation. There is still no batch collection/list route.

## P1.4 processing policy

P1.4 makes the following concrete lifecycle choices:

- Preflight and the first accepted start each consume one public source-admission event. Internal
  cases do not repeat source admission, so a legitimate batch cannot throttle itself.
- The start-key digest is scoped to the batch identifier. Reusing the same key returns the current
  batch representation with `202 Accepted` in every state; a different key cannot create a second
  job.
- The in-process registry owns task objects only. SQLite owns every browser-visible transition,
  selected-case count, terminal result, and bounded failure reason.
- At most two case workers run per batch, while every internal case also enters the same global
  two-slot extraction guard used by P0. Internal workers wait for that shared slot; public P0
  requests preserve their existing fail-closed admission behavior when both slots are occupied.
- Each live internal case has a stable case-UUID correlation identity. Every initial attempt and
  eligible retry uses the unchanged P0 reservation and settlement path, so retry accounting remains
  separate and durable.
- Provider, capacity, validation, and application failures terminate only the affected case with a
  safe category and reason. Other queued cases continue, and a batch with terminal case failures is
  still `completed` rather than a batch-wide failure.
- A claimed image is deleted after its case reaches a terminal state, whether extraction succeeds
  or fails. Images for cases explicitly not selected remain subject to the original 24-hour expiry.
- Shutdown stops accepting starts and drains active work for up to 15 seconds. Remaining task
  objects are cancelled and their uncertain cases become `interrupted`. Startup reconciles any
  prior `queued` or `processing` work to `interrupted`, settles reserved usage rows conservatively,
  deletes images for uncertain provider work, and never replays extraction.

## State and result contracts

Batch states are `draft`, `queued`, `processing`, `completed`, and `interrupted`. Expiry is deletion,
not a queryable state. Case states are `needs_correction`, `ready`, `queued`, `processing`,
`completed`, `failed`, `interrupted`, and `not_selected`.

The transition maps in `app.batches.contracts` are authoritative. Draft corrections may move a case
between `needs_correction` and `ready`. Start moves ready cases to `queued`; when the reviewer
explicitly starts ready cases only, the remaining invalid cases move to `not_selected`.
Queued and processing cases end independently. A provider or application failure is `failed`, not a
comparison outcome and not `Needs review`.

Polling returns at most 25 bounded case summaries containing:

- case ID and source row number;
- application ID and associated base filename;
- processing state and preflight issues;
- overall comparison outcome only for completed cases;
- processing duration when available; and
- one safe reason of at most 300 characters.

Expected and extracted details are excluded from polling summaries. The case-detail contract returns
the bounded expected input, validated `ExpectedReview` when available, and the existing five-check
`ReviewResult` only for completed work.

## P1.5 result and export policy

P1.5 makes the following concrete representation choices:

- Polling continues to return the original absolute expiry and never writes during retrieval. The
  encoded JSON representation is checked against the 256 KiB public limit before it is returned.
- A completed summary uses the first nonmatching or uncertain five-check reason as its short reason;
  an all-match result uses `All five checks matched.` Failed and interrupted cases retain their
  bounded safe processing reason. Provider request metadata and raw payloads are never projected.
- Case detail reuses the stored P0 `ReviewResult` unchanged, including exactly five check objects.
  Failed and interrupted cases return no fabricated comparison result.
- CSV download is available after the batch has been started, including while work remains active,
  so every selected case can be represented by its current state. Draft downloads return the
  bounded `batch_results_unavailable` conflict; `not_selected` cases are excluded.
- Result CSV is encoded as UTF-8 with a byte-order mark for common spreadsheet compatibility, uses
  CRLF records and standard CSV quoting, and downloads as the stable filename
  `label-review-results.csv` with `no-store` and `nosniff` headers.
- Multiple extracted candidates share one cell separated by ` | ` in provider order. Full
  Government Warning text remains excluded; only warning status is exported.
- Formula neutralization is applied to every rendered cell, not only fields currently expected to
  contain user or model text. Any leading `=`, `+`, `-`, `@`, tab, or carriage return receives an
  apostrophe before CSV quoting.

## P1.6 progress and results interface policy

P1.6 makes the following concrete interaction choices:

- Single and batch review use separate refresh-safe pages: `/` and `/batch`. The batch identifier
  remains a query parameter on `/batch`, and the production-shaped service explicitly serves the
  frontend entry point for direct `/batch` requests.
- The progress bar counts every selected case that reaches a terminal state (`completed`, `failed`,
  or `interrupted`) so it can reach its maximum under partial failure. Adjacent text separately
  reports successful completion as `completed / selected` and preserves the full state breakdown.
- Active batches poll at the server-requested interval bounded to one through two seconds. Temporary
  network and server failures retry exponentially up to eight seconds; terminal batches and
  unmounted views retain no polling timer. Other errors stop automatic polling and offer an explicit
  retry.
- The result list excludes deliberately `not_selected` rows but reports their count in the progress
  summary. Filters cover every selected case, exact `needs_review` outcomes, combined failed and
  interrupted states, and exact `all_checks_passed` outcomes.
- Terminal rows are keyboard-operable buttons. Selecting a completed row adapts the stored result
  metadata to the unchanged P0 comparison component; failed and interrupted rows display expected
  values and the bounded safe terminal reason without fabricating checks.
- Loading a selected case moves focus to its detail heading. The 25-row table stays inside a
  keyboard-focusable horizontal scroll region on narrow screens.
- The CSV action is shown only after the batch reaches a terminal state, even though the P1.5 API
  can represent an active batch. This keeps the primary reviewer download aligned with a stable
  terminal snapshot.

## API contract

P1.5 implements the complete planned P1 route surface:

```text
GET    /api/batch-template.xlsx
GET    /api/batch-template.csv
POST   /api/batches/preflight
GET    /api/batches/{batch_id}
GET    /api/batches/{batch_id}/cases/{case_id}
PATCH  /api/batches/{batch_id}/cases/{case_id}
PUT    /api/batches/{batch_id}/cases/{case_id}/image
POST   /api/batches/{batch_id}/start
GET    /api/batches/{batch_id}/results.csv
```

Batch and case IDs are UUIDv4 values. There is no collection-list endpoint.

`POST .../start` requires an `Idempotency-Key` of 16 through 128 characters. Only its SHA-256 digest
is stored. A first accepted start durably changes the draft to `queued` before returning
`202 Accepted`. Reusing the same key returns the existing representation with `202` and creates no
work or provider attempt. A different key after start
returns `409` with `batch_state_conflict`.

`all_cases` is accepted only when every case is ready. `ready_cases_only` explicitly marks remaining
invalid cases `not_selected`; at least one ready case is required.

An unknown, expired, malformed, or otherwise unavailable batch ID always returns `404` with code
`batch_not_found` and the message `The requested batch is unavailable.` This same bounded response
prevents callers from distinguishing expired content from another user's identifier. A case that
does not belong to the requested batch uses the same response. Polling never extends expiry.

## CSV export contract

The export contains one row per selected case and the columns defined by
`app.batches.export.CSV_EXPORT_COLUMNS`: application ID, processing state, outcome, duration, the
five check statuses, expected and extracted values for the four application fields, and a short
reason. The export omits full Government Warning text; warning status and the short reason are
sufficient for triage, while side-by-side warning detail remains in the case-detail response.

Every user- or model-derived cell beginning with `=`, `+`, `-`, `@`, tab, or carriage return is
prefixed with an apostrophe before normal CSV quoting. Output is UTF-8 with a stable header order and
one record per selected case.

## Additive schema and migration

`app.batches.schema` proposes schema version 2 and valid SQLite DDL for four content-bearing tables:

- `batch_reviews` for unguessable identity, lifecycle, absolute expiry, selection, and the start-key
  digest;
- `batch_images` for private storage keys, bounded image metadata, expiry, and cleanup bookkeeping;
- `batch_cases` for bounded reviewer content, validation issues, processing state, and an optional
  content-free provider correlation; and
- `batch_case_results` for the temporary structured comparison result.

These tables are distinct from `review_submissions` and `provider_attempts`, which remain the
content-free operational usage ledger. The migration only adds tables and indexes; it does not alter
or discard P0 rows. Database initialization applies it transactionally and updates `app_metadata`
from schema version 1 to 2 only after successful DDL execution.

All four batch tables carry or inherit an absolute expiry. Image files remain outside SQLite under
private unpredictable storage keys. Deleting a batch cascades through case content and results;
startup and periodic cleanup reconcile filesystem orphans independently.

## P1 requirement coverage

| Product requirement | P1.0 contract |
| --- | --- |
| XLSX or UTF-8 CSV plus images | Strict six-column templates and bounded package limits |
| No ZIP-only workflow | ZIP is absent from formats and routes |
| Preflight problems | Stable issue codes, locations, messages, and readiness states |
| Correct values or images | Patch and replacement routes plus mutable draft transitions |
| Process ready cases only | Explicit `ready_cases_only` start selection |
| Independent case outcomes | Separate case states and stored `ReviewResult` |
| Poll progress | Bounded summaries, exact counts, and 1.5-second interval |
| Inspect P0 detail | Separate case detail containing the existing five-check contract |
| Filter outcomes | Summary state, outcome, and short reason fields |
| Safe CSV | Fixed columns, no full warning text, and formula neutralization |
| No more than 24 hours | Absolute non-sliding expiry in response and schema contracts |
| Content-free operational records | Dedicated batch content tables and provider-neutral modules |
