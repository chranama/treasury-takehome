# Alcohol Label Verification Prototype

A standalone proof of concept that helps an alcohol-label reviewer compare expected application information with visible label artwork. The application is intended to reduce routine matching work while leaving ambiguous and regulatory decisions to a human reviewer.

## Project status

The P0 single-review workflow is implemented end to end with both a deterministic development adapter and a hosted OpenAI extraction adapter. Durable usage reservations, concurrency controls, idempotency, private cost limits, and explicit live-evaluation harnesses are implemented. The P0 application is deployed publicly, and its public smoke, browser-origin, performance, privacy, service-restart, and host-reboot recovery gates have passed.

The P1 batch workflow is implemented and deployed at `/batch`. It includes templates, bounded spreadsheet preflight, recoverable 24-hour drafts, an accessible correction interface, idempotent background processing, bounded progress polling, outcome filters, reusable P0 case detail, refresh recovery, safe terminal CSV export, periodic orphan cleanup, and restart reconciliation. Public preflight, correction, a mixed three-case live batch, a 25-case synthetic batch, processed-image deletion, and restart recovery have passed.

## Demo workflow

The core workflow allows a reviewer to:

1. enter the expected brand name, class/type, alcohol content, and net contents;
2. upload a label image;
3. review extracted values alongside the expected values;
4. check the mandatory Government Health Warning; and
5. identify matches, discrepancies, and cases requiring human review.

A bounded batch workflow at `/batch` can preflight and start as many as 25 ready applications while applying the same review independently to each selected case.

P1 is a bounded prototype workflow, not a production batch-processing system. It does not provide authentication, reviewer roles, audit history, durable cross-process queue resume, official COLAs Online integration, automatic approval or rejection, or demonstrated throughput for 200-300-application stakeholder batches.

## Deployed application

**URL:** [https://label-review.mealcheck.dev](https://label-review.mealcheck.dev)

The HTTPS deployment provides the working browser-based prototype without requiring local installation or access to this repository.

## Reviewer demo files

The interface links to a small synthetic demo bundle, so a reviewer does not need to create label
artwork or invent expected values.

For each P0 image, enter the same expected application values: brand `OLD TOM`, class/type
`Kentucky Straight Bourbon Whiskey`, ABV `45`, and net contents `750 mL`.

- [Matching label](frontend/public/demo/p0/matching-label.png): expect **All checks passed**.
- [Material net-contents mismatch](frontend/public/demo/p0/material-net-mismatch.png): expect
  **Needs review** because the artwork says `700 mL`.
- [Unreadable label](frontend/public/demo/p0/unreadable-label.png): expect **Needs review** rather
  than invented values.

The P1 page accepts one spreadsheet and separately selected images; do not create a ZIP file.

- Blank starting points: [XLSX template](frontend/public/demo/templates/label-review-batch.xlsx)
  and [CSV template](frontend/public/demo/templates/label-review-batch.csv).
- Valid two-case package: select [applications.csv](frontend/public/demo/p1/valid/applications.csv),
  [matching-label.png](frontend/public/demo/p1/valid/matching-label.png), and
  [material-net-mismatch.png](frontend/public/demo/p1/valid/material-net-mismatch.png). Expect two
  ready cases, then one pass and one `700 mL` mismatch when processed.
- Mixed preflight package: select
  [applications.csv](frontend/public/demo/p1/mixed-errors/applications.csv),
  [matching-label.png](frontend/public/demo/p1/mixed-errors/matching-label.png), and
  [replacement-label.png](frontend/public/demo/p1/mixed-errors/replacement-label.png). Expect one
  ready case, one case needing correction, and an unreferenced-image warning. Change `DEMO-FIX`
  ABV to `45`, then replace its missing image with `replacement-label.png` to make both rows ready.

All files are synthetic and contain no applicant data. Their source revisions, expected outcomes,
and SHA-256 hashes are recorded in `fixtures/reviewer-demo-v1.json`; regenerate them with
`uv run python -m evals.demo_bundle`.

The image-specific outcomes above describe the deployed hosted-extraction path. The default local
configuration uses the non-network `clear_matching_label` fake scenario: it returns fixed `OLD TOM`
observations and deliberately does not inspect the uploaded pixels. This makes local setup and UI
testing reproducible without an API key, but changing from the matching image to another demo image
does not change fake observations by itself.

## Local setup and run instructions

Prerequisites:

- Python 3.12;
- [`uv`](https://docs.astral.sh/uv/);
- Node.js 22 or newer; and
- npm.

Install the locked dependencies and create local configuration:

```bash
uv sync
npm --prefix frontend ci
cp .env.example .env
```

Run the FastAPI backend:

```bash
uv run uvicorn app.main:app --reload
```

In another terminal, run the Vite development server. It proxies `/api/*`, `/healthz`, and `/readyz` to the backend:

```bash
npm --prefix frontend run dev
```

For a production-shaped local run, compile the frontend first and then start the backend without `--reload`:

```bash
npm --prefix frontend run build
uv run uvicorn app.main:app
```

The backend then serves the compiled interface and API from the same origin. `GET /healthz` checks the process; `GET /readyz` checks local configuration, SQLite, and temporary storage without making a model request.

To exercise deterministic alternatives locally, stop the backend and restart it with one scenario
at a time:

```bash
TREASURY_FAKE_EXTRACTION_SCENARIO=mismatched_net_contents uv run uvicorn app.main:app
TREASURY_FAKE_EXTRACTION_SCENARIO=unreadable_image uv run uvicorn app.main:app
```

Use the same expected values shown above. These scenarios test mismatch and uncertainty handling;
they remain fixed observations rather than image extraction.

## Tests

Run the non-network backend and frontend checks:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
npm --prefix frontend run test
npm --prefix frontend run lint
npm --prefix frontend run build
```

The browser tests exercise the real frontend, multipart API, image preparation, deterministic comparison, and development adapter. Install Chromium once, then run them:

```bash
npm --prefix frontend exec -- playwright install chromium
npm --prefix frontend run test:e2e
```

Ordinary automated tests do not require an OpenAI key and must not make provider calls.

The versioned P1 package suite can also be materialized locally for inspection without making a
provider request:

```bash
uv run python -m evals.batch_suite \
  --materialize-dir .data/fixture-inspection/p1
```

This writes the reproducible CSV, XLSX, and image inputs described by
`fixtures/p1-packages-v1.json`. Generated binaries remain outside Git.

## Explicit P0 live evaluation

The P0 live evaluation is a separate, deliberately billable command. Configure `.env` with an OpenAI API key, then acknowledge the paid run and choose an evidence-file destination:

```bash
uv run python -m evals.live \
  --confirm-paid-run \
  --output .data/evaluations/luna-high.json
```

The default run makes four initial model requests over versioned synthetic fixtures, with at most one narrowly bounded retry per fixture. It records the exact configuration, fixture revision, check outcomes, uncertainty behavior, malformed-output rate, latency, provider token usage, and estimated cost. The command refuses to overwrite an existing evidence file. Use `--image-detail original` only as an explicit follow-up if warning transcription at the initial `high` setting fails.

To run the expanded 18-case F2 visual regression instead of the frozen four-case baseline, add
`--manifest fixtures/hosted-visual-v2.json`. This remains an explicitly paid evaluation; ordinary
tests cover the same rendering and gate logic with fixed responses and no provider access.

## Documentation

| Document | Focus |
| --- | --- |
| [Background](docs/background.md) | TTB context, existing workflow, and prototype opportunity |
| [Specification](docs/specification.md) | Product requirements, scope, acceptance scenarios, and limitations |
| [Backend](docs/backend.md) | Service architecture, deterministic comparison, validation, and reliability controls |
| [Frontend](docs/frontend.md) | React structure, interaction state, accessibility, and browser boundaries |
| [Storage](docs/storage.md) | SQLite responsibilities, image lifecycle, retention, cleanup, and logging boundaries |
| [Vision extraction](docs/vision-extraction.md) | Model observation contract, request configuration, failures, data handling, and limitations |
| [Deployment](docs/deployment.md) | Runtime topology, release process, health, network boundary, and P1 rollout state |
| [API](docs/api.md) | Routes, request contracts, idempotency, errors, and response limits |
| [Evaluation](docs/evaluation.md) | Test layers, versioned fixtures, hosted-model results, deployed measurements, and claim boundaries |
| [Workflows](docs/workflows.md) | P0 and P1 inputs, preflight, state transitions, processing, results, and examples |
| [macOS deployment assets](deploy/macos/README.md) | Exact release, installation, restart, rollback, and smoke-test commands |
