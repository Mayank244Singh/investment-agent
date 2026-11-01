# Project Status

## Milestone 1: Project Foundation

- [x] Inspect the repository and development environment.
- [x] Review and approve the Milestone 1 foundation plan.
- [x] Add a `.gitignore` for local environments, secrets, generated data, caches, build output, coverage, and OS/editor files.
- [x] Create the modular backend structure, including `api`, `core`, `db`, `schemas`, and `services` layers.
- [x] Configure FastAPI with environment-based settings.
- [x] Configure structured logging with request IDs and central exception handlers.
- [x] Configure SQLite and SQLAlchemy.
- [x] Add a health-check endpoint and backend tests, including database-unavailable handling and configuration loading.
- [x] Configure Ruff and mypy with pinned development dependency versions.
- [x] Create the React, TypeScript, and Vite frontend with exact pinned dependency versions.
- [x] Add ESLint and frontend tests with exact pinned dependency versions.
- [x] Create the minimal dashboard shell and backend connectivity indicator.
- [x] Add an ADR requiring the risk module to independently validate every proposed trade.
- [x] Create the README with setup, architecture, testing, and run instructions.
- [x] Run backend and frontend tests, builds, startup checks, and frontend-to-backend verification.

## Decisions

- Work is limited to Milestone 1 until explicit approval for the next phase.
- No trading strategies, predictions, broker integrations, orders, paid APIs, or cloud deployment are in scope.
- Dependencies will be project-local and pinned to exact versions.
- Future schema changes require Alembic before the first durable domain model is released; see ADR 0001.
- The frontend reaches the backend through Vite's `/api` proxy, verified manually outside the sandbox.

## Known Issues

- The execution sandbox denies local port binding (`EPERM`) for Uvicorn and Vite. End-to-end proxy verification must be run manually in a local terminal; the user has verified it succeeds there.

## Next Task

Run complete backend/frontend validation and manually verify the new ledger write endpoints end to end.

## Phase 2: Portfolio Ledger and Risk Validator

- [x] Approve FIFO, append-only ledger semantics, risk-capital definitions, risk defaults, timezone, stop-loss behavior, and no-short-selling rules.
- [x] Add and exact-pin Alembic; configure migrations from environment settings.
- [x] Add integer-paise ledger models for capital movements, transactions, FIFO lots, realizations, and Profit Vault entries.
- [x] Add the initial migration with UTC timestamps, fees, constraints, indexes, and foreign keys.
- [x] Verify migration upgrade and downgrade on a temporary SQLite database.
- [x] Implement and test append-only FIFO ledger services for deposits, withdrawals, buys, sells, fees, multi-lot and partial FIFO matching, realised P&L, atomic rollback, and Profit Vault credits.
- [x] Add and test a read-only ledger endpoint and validation-only ProposedTrade endpoint; validation creates no order or ledger record.
- [x] Add manual append-only ledger POST endpoints for deposits, withdrawals, buys, and sells, with persistent idempotency keys and API tests.
- [x] Implement and test the database-independent, fail-closed risk validator with config-sourced limits and all-reasons rejection results.
- [x] Add and test Phase 2 portfolio summary and risk-rule status UI backed by real API data, with empty and error states.
- [x] Record FIFO, Profit Vault, fail-closed risk rules, and known limitations in ADR 0002; update the README for Phase 2.
- [x] Run complete backend/frontend validation and manual end-to-end verification.

### Phase 2 Decisions

- FIFO cost basis; all money is stored as integer paise and cash-equity quantities are whole positive integers.
- Ledger records are append-only. Corrections use reversing entries; there are no edit or delete operations.
- Buy fees are included in lot cost. Sell fees reduce proceeds. Sell matching, realizations, and a positive Profit Vault entry were committed atomically.
- Profit Vault receives only positive realised profit and is not reduced by later losses.
- Risk capital is cash plus open lots at cost minus Profit Vault balance; deployable cash is cash minus Profit Vault balance.
- Approved configurable defaults: 10% order, 20% single-stock allocation, 1% loss per trade, and 2% daily loss.
- Daily realised loss resets at midnight Asia/Kolkata while persisted timestamps remain UTC.
- Stop-loss is required for buys and must be positive and below entry price. Sells cannot exceed held quantity; short selling is prohibited.
- The risk validator receives a portfolio snapshot and never queries the database.
- Historical ledger entries bypass risk validation by design and reject only impossible withdrawals or sells; optional idempotency keys prevent duplicate events.
- Backend development binding remains `127.0.0.1`; authentication is not yet implemented and must be documented.
