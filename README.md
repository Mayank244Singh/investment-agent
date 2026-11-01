# Investment Agent

A modular foundation for an Indian-market investment and portfolio-management application. This milestone provides a FastAPI health API, local SQLite connectivity, and a React dashboard. It does not provide market data, investment advice, forecasts, trading strategies, broker integration, or order execution.

## Prerequisites

- macOS with Python 3.12 and the existing `.venv`
- Node.js 26 and npm

## Install

From the repository root:

```sh
.venv/bin/python -m pip install -r backend/requirements-dev.txt
cd frontend && npm install
```

## Run locally

Use two terminals from the repository root.

```sh
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload
```

```sh
cd frontend && npm run dev
```

The frontend development server proxies `/api` requests to the backend at `http://127.0.0.1:8000`. The health endpoint is `GET /api/v1/health`. The proxy connection was verified manually outside the execution sandbox; port binding is blocked inside that sandbox.

## Test, lint, and build

```sh
# Backend tests
cd backend && ../.venv/bin/pytest

# Frontend tests
cd frontend && npm test

# Backend static checks
cd backend && ../.venv/bin/ruff check app tests && ../.venv/bin/mypy app tests

# Frontend lint
cd frontend && npm run lint

# Frontend production build
cd frontend && npm run build
```

## Architecture

The backend separates HTTP routes, services, schemas, and database infrastructure. Routes call services; services own application behavior; schemas define API contracts; and database code is isolated in `db`. Configuration is environment-based, logs are structured JSON with request IDs, and central exception handlers produce consistent error responses.

The frontend is a React/TypeScript dashboard. Its typed API client calls the health endpoint through Vite’s `/api` proxy and renders loading, connected, and unreachable states without displaying fabricated market or portfolio figures.

## Layout

```text
backend/
  app/api/          HTTP routes
  app/core/         settings, logging, request IDs, exception handling
  app/db/           SQLAlchemy engine and session lifecycle
  app/schemas/      API request and response contracts
  app/services/     application behavior
  tests/            backend tests
frontend/
  src/api/          typed backend client
  src/              React dashboard and tests
docs/adr/           architecture decisions
data/               local SQLite data (ignored by Git)
```

## Planned extension points

- Authorized market-data ingestion and historical storage
- Portfolio, profit-vault, and performance models
- Independent risk validation before every paper or broker order
- Research, fundamentals, ranking, rotation, backtesting, and paper trading
- Broker integration only after explicit approval and regulatory review

See [the architecture decision record](docs/adr/0001-architecture.md) for the risk-first rule and migration decision.
