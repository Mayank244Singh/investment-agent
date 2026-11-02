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
- Phase 4's dashboard panels have automated API and UI tests, but a browser check against an imported temporary database is still needed outside the sandbox.
- The bhavcopy profile was observed in five samples, not verified against an official specification; the user must verify `ClsPric` semantics and source terms before real use.

## Next Task

Run a user-reviewed manual dashboard check against a temporary imported database, then seek approval before any next milestone or real-data migration.

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

## Phase 3: Hardening

- [x] Approve backup, authentication, negative-cash integrity, local API documentation, restore, and loopback-only development decisions.
- [x] Add configurable SQLite online backups, retention, guarded atomic restore, and temporary-database tests.
- [x] Require bearer-token authentication for every API route except health and add loopback/CORS safeguards.
- [x] Add the read-only ledger integrity CLI and corruption tests.
- [x] Add README security and restore guidance plus ADR 0003 and complete automated Phase 3 validation.

### Phase 3 Decisions

- Backups default to the git-ignored `backups/` directory, retain the newest 10 files, and use SQLite's online backup API.
- Restore requires `--confirm`, validates SQLite integrity and the Alembic revision, creates a pre-restore safety backup, restores through a temporary database, and atomically swaps the file.
- Backup and restore tests use temporary databases only; operational commands are never tested against `data/`.
- Backups contain financial records and must be copied regularly to a private external drive rather than a public or shared cloud folder.
- Authentication tokens require at least 32 characters, remain in the git-ignored `.env`, and must never be logged, returned, or embedded in frontend assets.
- Authentication fails closed unless explicitly disabled on a loopback host; unauthorised responses are generic bearer challenges.
- API documentation is available only for the exact `127.0.0.1` backend host, and CORS permits only `http://127.0.0.1:5173`.
- The Vite development server binds to `127.0.0.1` and injects the non-`VITE_` token only in its server-side proxy.
- Negative final cash is an integrity failure indicating a likely missing deposit; intermediate dated balances are not checked.

## Phase 4: Historical end-of-day prices

- [x] Add instruments, daily_prices, and import_batches models with database constraints and a migration tested for upgrade and downgrade on a temporary database.
- [x] Implement the local ZIP importer and CLI with strict observed-profile headers, date checks, Decimal-to-paise parsing, EQ-only storage, conflict detection, and optional skip-bad-rows reporting; verify with synthetic fixtures and temporary databases.
- [x] Add as-of valuation with a configurable five-calendar-day staleness limit, priced and unpriced totals, and explicit unavailable reasons.
- [x] Add authenticated read APIs for valuation and price coverage, with temporary-database tests.
- [x] Display end-of-day price coverage and per-holding valuation in the dashboard, with empty, unavailable, stale, and API error states; verify frontend tests, lint, and build.
- [x] Extend the read-only integrity command for price, instrument, date, and import-batch discrepancies, with corruption tests on temporary databases.
- [x] Update README with the daily manual import routine, unavailable semantics, and staleness configuration; record the observed profile and limitations in ADR 0004.
- [x] Complete automated Phase 4 backend and frontend validation, static checks, production build, and Git tracking audit.

### Phase 4 Decisions

- The supported import profile is based on five observed samples and is not verified against an official specification. Only exact headers will be accepted.
- Instruments use normalized uppercase symbols and EQ series; a changed ISIN for an existing symbol and series will stop import.
- Prices are unadjusted and stored as integer paise; each price retains its import batch for source and file provenance.
- Import defaults to all-or-nothing, with an optional skip-bad-rows report mode. ZIP members will be read in memory.
- No real exchange data or sample files are copied into the repository.
