# Alcohol Label Verification Prototype

A standalone proof of concept that helps an alcohol-label reviewer compare expected application information with visible label artwork. The application is intended to reduce routine matching work while leaving ambiguous and regulatory decisions to a human reviewer.

## Project status

The repository contains the application scaffold and operational health checks. The P0 single-review workflow is the current implementation priority.

## Planned demo

The core workflow will allow a reviewer to:

1. enter the expected brand name, class/type, alcohol content, and net contents;
2. upload a label image;
3. review extracted values alongside the expected values;
4. check the mandatory Government Health Warning; and
5. identify matches, discrepancies, and cases requiring human review.

A bounded batch workflow is also planned to demonstrate how the same review could be applied to multiple applications.

## Deployed application

**URL:** [https://label-review.mealcheck.dev](https://label-review.mealcheck.dev) is the planned deployed URL; however, the project is not yet deployed.

The submitted deployment will provide the working browser-based prototype without requiring local installation or access to this repository.

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

The browser-test scaffold is also available. Install Chromium once, then run it:

```bash
npm --prefix frontend exec -- playwright install chromium
npm --prefix frontend run test:e2e
```

Ordinary automated tests do not require an OpenAI key and must not make provider calls.

## Documentation

- [Project background](docs/background.md)
- [Demo specification and assumptions](docs/specification.md)
- [Implementation approach, tools, and assumptions](docs/implementation.md)
