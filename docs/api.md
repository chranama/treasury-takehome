# API

**Status:** P0 and P1 contracts implemented

## Conventions

The API is an application boundary for the same-origin browser, not a general public integration
platform. FastAPI's interactive documentation is disabled in the deployed app. Pydantic models in
[`app/api`](../app/api), [`app/comparison/models.py`](../app/comparison/models.py), and
[`app/batches/contracts.py`](../app/batches/contracts.py) are the executable contracts.

All error responses are bounded and include a correlation ID and processing duration. The response
also carries `X-Correlation-ID`. Batch and case identifiers are canonical UUIDv4 strings. There is
no collection endpoint for discovering batches.

## Routes

| Method and path | Success | Purpose |
| --- | --- | --- |
| `GET /healthz` | `200` | Process liveness |
| `GET /readyz` | `200` or `503` | Configuration, schema, database, and temporary-storage readiness |
| `POST /api/reviews` | `200` | Validate and process one label |
| `GET /api/batch-template.xlsx` | `200` | Download the XLSX template |
| `GET /api/batch-template.csv` | `200` | Download the CSV template |
| `POST /api/batches/preflight` | `201` | Validate a spreadsheet and selected images; create a short-lived draft |
| `GET /api/batches/{batch_id}` | `200` | Retrieve bounded progress and case summaries |
| `GET /api/batches/{batch_id}/cases/{case_id}` | `200` | Retrieve expected values and terminal detail for one case |
| `PATCH /api/batches/{batch_id}/cases/{case_id}` | `200` | Correct the four expected values in a draft case |
| `PUT /api/batches/{batch_id}/cases/{case_id}/image` | `200` | Replace one draft case's image |
| `POST /api/batches/{batch_id}/start` | `202` | Idempotently start all or only ready cases |
| `GET /api/batches/{batch_id}/results.csv` | `200` | Download selected cases after start |

Batch retrieval and result responses use `Cache-Control: no-store`. Template and result downloads
have fixed attachment filenames; CSV results also set `X-Content-Type-Options: nosniff`.

## Single-review request

`POST /api/reviews` accepts multipart form data:

| Field | Contract |
| --- | --- |
| `brand_name` | Required text, at most 200 characters |
| `class_type` | Required text, at most 200 characters |
| `expected_abv` | Decimal from 0 through 100 |
| `expected_net_contents` | Positive decimal |
| `expected_net_contents_unit` | `mL` or `L` |
| `image` | Exactly one accepted image |
| `Idempotency-Key` header | 16–128 characters |

The success response contains the overall outcome, exactly five check results, processing duration,
correlation ID, and `synthetic` or `live` processing mode. A repeated idempotency key returns a safe
conflict rather than replaying content that the application deliberately does not retain.
The deployed live gate stores only a durable SHA-256 digest of the key. The non-production fake gate
stores only an in-memory digest and therefore enforces the same conflict contract for the lifetime
of that local process; restarting fake mode resets its development-only registry.

## Batch preflight and correction

Preflight accepts one `.xlsx` or UTF-8 `.csv` spreadsheet and zero to 25 images. A structurally
valid package creates a draft even when every row needs correction and returns `201` with a
`Location` header. Structural package errors return `413` or `422` without a draft.

Preflight issues have stable machine codes, severity, scope, and optional row and field locations.
Messages are derived from those codes rather than uploaded text. An unreferenced valid image is a
warning; other issue classes prevent the affected row or package from being ready.

Case correction accepts only the four expected values. Image replacement targets the case UUID
directly, validates exactly one image, and leaves the previous association unchanged if validation
fails.

## Batch start and polling

`POST /api/batches/{batch_id}/start` requires an `Idempotency-Key` and one selection:

- `all_cases`, accepted only when every case is ready; or
- `ready_cases_only`, which marks remaining invalid cases `not_selected`.

The first accepted key durably queues the selection before returning `202`. Reusing that key returns
the current representation and creates no second job or provider attempt. A different key after
start returns `409 batch_state_conflict`.

Polling returns no more than 25 case summaries and is capped at 256 KiB encoded. Summaries contain
identity, source row, state, issues, terminal outcome, duration, and one short reason—not complete
expected or extracted values. Case detail supplies those values only when requested.

CSV export is available after start and contains one selected case per row. The interface exposes
the download only for a terminal batch. Full Government Warning text is omitted, and every rendered
cell is neutralized when it begins with a spreadsheet-formula prefix.

## Error and availability behavior

Single-review categories distinguish invalid input, disabled extraction, capacity, throttle,
duplicate submission, timeout, provider availability, malformed provider output, and internal
failure. The API maps them to bounded `4xx` or `5xx` responses without stack traces or provider
payloads.

Malformed, unknown, expired, and cross-batch identifiers all return the same `404 batch_not_found`
shape. This avoids revealing whether another unguessable identifier exists. Draft conflicts and
unavailable processing return stable `409` or `503` batch codes.

The prototype has no authentication. UUIDs, same-origin use, absence of enumeration, short
retention, source throttling, and bounded cost controls limit public-demo exposure but do not
constitute user authorization.

Package limits and workflow behavior are described in [Workflows](workflows.md); data lifetime is
described in [Storage](storage.md).
