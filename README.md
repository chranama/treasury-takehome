# Alcohol Label Verification Prototype

A standalone proof of concept that helps an alcohol-label reviewer compare expected application information with visible label artwork. The application is intended to reduce routine matching work while leaving ambiguous and regulatory decisions to a human reviewer.

## Project status

The P0 single-review workflow is implemented end to end with both a deterministic development adapter and a hosted OpenAI extraction adapter. Durable usage reservations, concurrency controls, idempotency, private cost limits, and explicit live-evaluation harnesses are implemented. The P0 application is deployed publicly, and its public smoke, browser-origin, performance, privacy, service-restart, and host-reboot recovery gates have passed.

## Demo workflow

The core workflow allows a reviewer to:

1. enter the expected brand name, class/type, alcohol content, and net contents;
2. upload a label image;
3. review extracted values alongside the expected values;
4. check the mandatory Government Health Warning; and
5. identify matches, discrepancies, and cases requiring human review.

A bounded batch workflow is planned to demonstrate how the same review could be applied to multiple applications.

## Deployed application

**URL:** [https://label-review.mealcheck.dev](https://label-review.mealcheck.dev)

The HTTPS deployment provides the working browser-based prototype without requiring local installation or access to this repository.

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

## Explicit live evaluation

The live evaluation is a separate, deliberately billable command. Configure `.env` with an OpenAI API key, then acknowledge the paid run and choose an evidence-file destination:

```bash
uv run python -m evals.live \
  --confirm-paid-run \
  --output .data/evaluations/luna-high.json
```

The default run makes four initial model requests over versioned synthetic fixtures, with at most one narrowly bounded retry per fixture. It records the exact configuration, fixture revision, check outcomes, uncertainty behavior, malformed-output rate, latency, provider token usage, and estimated cost. The command refuses to overwrite an existing evidence file. Use `--image-detail original` only as an explicit follow-up if warning transcription at the initial `high` setting fails.

## Documentation

- [Project background](docs/background.md)
- [Demo specification and assumptions](docs/specification.md)
- [Implementation approach, tools, and assumptions](docs/implementation.md)
- [Native macOS deployment assets](deploy/macos/README.md)
- [Fixture coverage and manifest contract](docs/fixture-coverage.md)
