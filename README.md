# Investment Agent

A local Indian-market portfolio-management foundation with an append-only FIFO ledger, realised P&L, a Profit Vault, independent trade-risk validation, bearer authentication, verified SQLite backups, guarded restore, and read-only integrity checks. It does not provide market data, investment advice, predictions, trading strategies, broker integration, or order execution.

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

Create the private local configuration, generate a token, and restrict its file permissions:

```sh
cp .env.example .env
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# Paste the generated value after AUTH_TOKEN= in .env, then:
chmod 600 .env
```

Never commit `.env`, print the token, or place it in frontend source or a `VITE_` variable.

## Run locally

Use two terminals from the repository root.

```sh
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

```sh
cd frontend && npm run dev
```

The backend refuses to start without `AUTH_TOKEN` unless `AUTH_DISABLED=true` is explicitly configured on a loopback host. Every API route except health requires the token. Keep both servers bound to `127.0.0.1`.

The Vite development server reads the non-`VITE_` token only in its Node-side configuration and adds the bearer header while proxying `/api` to `http://127.0.0.1:8000`. This is a **development-only** authentication approach: anyone who can reach the Vite port can use the authenticated proxy. Never expose port 5173 to a network, and do not use this proxy design for deployed static assets.

## Current API

- `GET /api/v1/health`: database health check
- `GET /api/v1/portfolio/ledger`: append-only ledger and book-cost summary
- `GET /api/v1/risk/rules`: validated non-secret risk configuration
- `POST /api/v1/risk/validate`: validate a `ProposedTrade`; nothing is executed or stored as an order
- `POST /api/v1/portfolio/deposits`: append a historical deposit
- `POST /api/v1/portfolio/withdrawals`: append a historical withdrawal
- `POST /api/v1/portfolio/buys`: append a historical buy
- `POST /api/v1/portfolio/sells`: append a historical sell

The health endpoint is public. All other listed endpoints require `Authorization: Bearer <token>`. Ledger writes record historical events and do not execute orders.

## Backup, integrity, and restore

Create a verified online backup while SQLite is in use:

```sh
PYTHONPATH=backend .venv/bin/python -m app.cli backup
```

Backups default to the git-ignored `backups/` directory and retain the newest 10 files. Configure these with `BACKUP_DIRECTORY` and `BACKUP_RETENTION_COUNT`. Backups contain financial records. Copy them regularly to a private external drive; do not use a public or shared cloud folder.

Run the ledger reconciliation without modifying the database:

```sh
PYTHONPATH=backend .venv/bin/python -m app.cli integrity
```

The integrity command opens SQLite with `mode=ro`, lists every discrepancy, and exits non-zero on failure. Negative final cash is treated as a likely missing deposit entry.

To restore:

1. Stop the backend and frontend.
2. Keep the existing database and selected backup on the same local machine.
3. Run the guarded restore:

   ```sh
   PYTHONPATH=backend .venv/bin/python -m app.cli restore backups/investment_agent_TIMESTAMP.db --confirm
   ```

4. The command verifies SQLite integrity and the Alembic revision, creates a pre-restore safety backup, restores through a temporary file, and atomically swaps the database.
5. Apply any migration newer than the restored backup, then verify integrity:

   ```sh
   .venv/bin/alembic -c backend/alembic.ini upgrade head
   PYTHONPATH=backend .venv/bin/python -m app.cli integrity
   ```

6. Restart both servers and verify the dashboard before entering more records.

## Security

- Use a unique token of at least 32 characters and protect `.env` with mode `600`.
- Authentication fails closed. `AUTH_DISABLED=true` is allowed only for loopback development and logs a prominent warning.
- Bind FastAPI and Vite only to `127.0.0.1`. CORS permits only `http://127.0.0.1:5173`.
- API documentation is available only when the configured backend host is exactly `127.0.0.1`.
- The Vite token-injecting proxy is development-only and must never be network-exposed.
- Tokens are compared as bytes in constant time and are never returned or logged.
- Keep local and external backups private because they contain complete financial records.

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

The backend separates HTTP routes, services, schemas, domain modules, and database infrastructure. Routes call services; portfolio and risk business rules live in `modules`; schemas define API contracts; and database code is isolated in `db`. Configuration is environment-based, logs are structured JSON with request IDs, central exception handlers produce consistent error responses, and authentication is applied at the protected API-router boundary.

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
backups/            verified local SQLite backups (ignored by Git)
```

## Known limitations

- Authentication is a single shared local token, not a user, session, role, or token-rotation system.
- The Vite development proxy provides full API access to anything that can reach port 5173; loopback binding is mandatory.
- Local backups are on the same disk as the database and do not protect against loss of the Mac until copied to a private external drive.
- Backups are not application-encrypted. Protect the Mac, `.env`, backup directory, and external drive with OS-level security.
- Restore must be run with the servers stopped; the CLI does not terminate running application processes.
- Integrity checks detect accounting and SQLite inconsistencies but do not provide cryptographic tamper evidence.
- There is still no unrealised P&L or verified market-price source.

## Planned extension points

- Authorized market-data ingestion and historical storage
- Additional portfolio and performance reporting
- Independent risk validation before every paper or broker order
- Research, fundamentals, ranking, rotation, backtesting, and paper trading
- Broker integration only after explicit approval and regulatory review

See [ADR 0001](docs/adr/0001-architecture.md) for the original layering decision, [ADR 0002](docs/adr/0002-portfolio-ledger-and-risk.md) for FIFO accounting and fail-closed risk validation, and [ADR 0003](docs/adr/0003-hardening.md) for authentication, backup, restore, and integrity decisions.
