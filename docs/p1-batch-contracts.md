# P1 Batch Contracts

**Status:** Milestone P1.0 contract with P1.1 parsing and P1.2 draft persistence

**Schema proposal version:** 2

**Last updated:** 2026-08-12

## Milestone boundary

P1.0 defines provider-neutral domain contracts, deterministic blank templates, API behavior,
export safety, and an additive database proposal. P1.1 implements bounded workbook preflight, and
P1.2 applies the additive migration and persists short-lived drafts. Batch routes, reviewer UI,
provider work, and result APIs remain P1.3 through P1.5 work.

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

## API contract

The planned route surface is:

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
`202 Accepted`. Reusing the same key returns the existing representation—`202` while active and
`200` after a terminal state—and creates no work or provider attempt. A different key after start
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
