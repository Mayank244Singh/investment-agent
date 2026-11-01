# Investment Agent

A modular foundation for an Indian-market investment and portfolio-management application. Phase 2 adds an append-only FIFO portfolio ledger, realised P&L, a Profit Vault, and independent validation of proposed trades. It does not provide market data, investment advice, predictions, trading strategies, broker integration, or order execution.

## Prerequisites

- macOS with Python 3.12 and the existing `.venv`
- Node.js 26 and npm

## Install

From the repository root:

```sh
.venv/bin/python -m pip install -r backend/requirements-dev.txt
cd frontend && npm install
```

Apply the database migration from the repository root:

```sh
.venv/bin/alembic -c backend/alembic.ini upgrade head
```

## Run locally

Use two terminals from the repository root.

```sh
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```sh
cd frontend && npm run dev
```

The frontend development server proxies `/api` requests to the backend at `http://127.0.0.1:8000`. Keep the backend bound to `127.0.0.1`: **there is no authentication or authorization yet**, so it must not be exposed to a network. The proxy connection was verified manually outside the execution sandbox; port binding is blocked inside that sandbox.

## Current API

- `GET /api/v1/health`: database health check
- `GET /api/v1/portfolio/ledger`: append-only ledger and book-cost summary
- `GET /api/v1/risk/rules`: validated non-secret risk configuration
- `POST /api/v1/risk/validate`: validate a `ProposedTrade`; nothing is executed or stored as an order

Manual ledger write endpoints are not implemented yet. Ledger data currently enters through the domain service and tests.

## Test, lint, and build

```sh
# Backend tests
cd backend && ../.venv/bin/pytest

# Frontend tests
cd frontend && npm test

# Backend static checks
cd backend && ../.venv/bin/ruff check app alembic tests && ../.venv/bin/mypy app alembic tests

# Frontend lint
cd frontend && npm run lint

# Frontend production build
cd frontend && npm run build
```

## Architecture

The backend separates HTTP routes, services, schemas, domain modules, and database infrastructure. Routes call services; portfolio and risk business rules live in `modules`; schemas define API contracts; and database code is isolated in `db`. Configuration is environment-based, logs are structured JSON with request IDs, and central exception handlers produce consistent error responses.

Money is stored as integer paise. Buys create FIFO lots including buy fees; sells consume those lots atomically, subtract sell fees from proceeds, record realised P&L, and credit only positive sale profit to the Profit Vault. There is no unrealised P&L because no verified market-price source exists.

The risk validator receives a portfolio snapshot and never queries the database. Its limits come from validated settings, it returns every rejection reason, and it fails closed when configuration, inputs, snapshot data, or rule execution is invalid.

The frontend is a React/TypeScript dashboard. Its typed client reads the ledger and risk-rule endpoints through Vite’s `/api` proxy. It shows real book-cost data, “No data” for an empty ledger, and “Unavailable” on API errors; it does not fabricate values.

## Layout

```text
backend/
  app/api/          HTTP routes
  app/core/         settings, logging, request IDs, exception handling
  app/db/           SQLAlchemy models, engine, and session lifecycle
  app/modules/      portfolio and independent risk domain logic
  app/schemas/      API request and response contracts
  app/services/     API orchestration
  alembic/          reviewed schema migrations
  tests/            backend tests
frontend/
  src/api/          typed backend client
  src/              React dashboard and tests
docs/adr/           architecture decisions
data/               local SQLite data (ignored by Git)
```

## Planned extension points

- Authorized market-data ingestion and historical storage
- Manual append-only ledger input endpoints
- Additional portfolio and performance reporting
- Independent risk validation before every paper or broker order
- Research, fundamentals, ranking, rotation, backtesting, and paper trading
- Broker integration only after explicit approval and regulatory review

See [ADR 0001](docs/adr/0001-architecture.md) for the original layering decision and [ADR 0002](docs/adr/0002-portfolio-ledger-and-risk.md) for FIFO accounting and fail-closed risk validation.
