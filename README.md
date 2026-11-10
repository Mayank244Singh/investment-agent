# Investment Agent

A local Indian-market portfolio manager with an append-only FIFO ledger, realised P&L, a Profit Vault, independent trade-risk validation, bearer authentication, SQLite backups, guarded restore, read-only integrity checks, and manually imported end-of-day prices. A separate, isolated virtual demo supports risk-gated simulated fills on explicitly synthetic prices. It does not provide live market data, investment advice, broker integration or live order execution.

For the optional ₹10,000 virtual demo setup, isolated migration/seed commands, risk rules and limitations, see `docs/STAGE_C2_VIRTUAL_ORDER_IMPLEMENTATION.md`. The demo never uses provisional FYERS candles or canonical market prices.

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

`DATABASE_URL` in `.env` selects the SQLite file; the CLI's `--database` option overrides that setting for one command.

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

## Daily manual price routine

After each trading day, check the data source's terms yourself, download **one** supported cash-market bhavcopy ZIP manually, and keep it outside the repository (for example in `../sample-data/`). This daily routine does not download prices automatically. Once the Phase 4 migration has been applied, import the file from the repository root:

```sh
.venv/bin/alembic -c backend/alembic.ini upgrade head
PYTHONPATH=backend .venv/bin/python -m app.cli import-prices ../sample-data/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip --source-label "NSE manual bhavcopy"
PYTHONPATH=backend .venv/bin/python -m app.cli integrity
```

Replace `YYYYMMDD` with the trading date. The importer accepts only its exact observed ZIP and CSV profile, reads the ZIP in memory, stores EQ rows only, reports skipped series and rejected rows, and commits a valid file atomically. The same file hash is a no-op. `--skip-bad-rows` is optional; without it, any bad EQ row rejects the whole file. Check the **Price coverage** panel in the local dashboard for the latest date per symbol, source label, file name, and import counts. Use `--database /path/to/copy.db` to target a disposable database for practice.

An **Unavailable** holding has no EQ close on or before the as-of date or its last close is older than `PRICE_STALENESS_DAYS` calendar days (default 5). The dashboard shows the reason and last price date when one exists; it never estimates or inserts a zero value. Market value and unrealised P&L totals cover priced holdings only, and unpriced holdings' FIFO cost is shown separately. Every displayed price is labelled end-of-day and dated.

## Offline Upstox comparison pilot

Phase 6B.1 compares mocked Historical Candle V3 responses with existing NSE canonical prices for **RELIANCE, TCS, INFY, HDFCBANK and ICICIBANK**. Their common stored NSE dates are 26–30 October 2026: five sessions and 25 reference observations. The offline command reads the reference database and writes observations only to a new temporary database. It needs no Upstox token and makes no provider request.

From the repository root, replace the three angle-bracket placeholders with absolute local paths. The destination must be a new temporary database, and the mock JSON stays outside the repository:

```sh
PYTHONPATH=backend .venv/bin/python -m app.cli pilot-upstox --mode offline \
  --reference-database '<existing-db>' \
  --database '<new-temp-db>' \
  --mock-file '<mock-json>'
```

The mock JSON has `mode: "synthetic_mock"`, an `instruments` list of five explicitly labelled `mock_instrument_master` records, and a `responses` object keyed by the five symbols. Each instrument record supplies `instrument_key`, `trading_symbol`, `exchange`, `segment`, `instrument_type` and `isin`; each response uses `status: "success"` and `data.candles` with timestamp, open, high, low, close, volume and open interest. The values and keys are synthetic test inputs, not verified Upstox identities or exchange data.

Use `--symbols TESTA TESTB TESTC TESTD TESTE` to substitute another set of exactly five EQ symbols in a synthetic reference database; the fixture must use those same symbols. The destination must not exist before the command and must be under the operating system's temporary directory. Remove that destination after reviewing the report. Run the offline checks with `.venv/bin/python -m pytest backend/tests/test_upstox_historical_provider.py backend/tests/test_upstox_pilot_service.py backend/tests/test_upstox_pilot_cli.py backend/tests/test_upstox_reconciliation.py -q`.

The report compares exact integer-paise OHLC and integer volume, with no tolerance. `EXACT_MATCH`, `PRICE_MATCH_VOLUME_DIFF` and `PRICE_DIFF` describe comparable sessions; `MISSING_NSE` and `MISSING_UPSTOX` show one-sided coverage; `REVISION_AMBIGUOUS` means multiple provider observations exist for one instrument and date, so none is chosen automatically. Differences list the source values and signed change. Mock results cannot establish real provider quality. The existing NSE canonical prices remain the valuation and backtest source. The daily candle timestamp is retained in the raw observation, but it does not establish when the completed bar became available; event and availability times stay unknown, and no previous close is invented.

The adapter's daily path, bearer header and candle field order were checked against the official Upstox Historical Candle V3 documentation in Phase 6B.2. The CLI permits only the five approved live pilot symbols with explicit `--mode live --allow-live`, a fresh temporary database and a private `UPSTOX_ANALYTICS_TOKEN` loaded from the backend's git-ignored `.env`. It makes at most five historical GETs, with one attempt each and no automatic retries or fallback broker requests. **Separately authorized Historical Candle V3 and LTP V3 diagnostics both returned HTTP 403.** The authorization issue remains unresolved; this opt-in pilot is not a production-ready market-data source, and no broker execution is enabled. See the [pilot report](docs/reports/UPSTOX_PHASE_6B2_PILOT.md). Keep the token out of frontend variables, reports, logs and Git. The right to retain or train on Upstox data remains unresolved.

## Current API

- `GET /api/v1/health`: database health check
- `GET /api/v1/portfolio/ledger`: append-only ledger and book-cost summary
- `GET /api/v1/portfolio/valuation`: dated end-of-day valuation and priced/unpriced totals
- `GET /api/v1/portfolio/realized-pnl`: FIFO realised P&L by symbol and match, with optional `from`, `to`, or `financial_year` filter
- `GET /api/v1/portfolio/ledger.csv` and `GET /api/v1/portfolio/fifo-matches.csv`: private, read-only CSV exports
- `GET /api/v1/prices/coverage`: latest EQ dates and import-batch provenance
- `GET /api/v1/prices/latest?symbol=...`: dated close for a non-blocking manual-entry warning
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

The integrity command opens SQLite with `mode=ro`, checks both ledger and imported price data, lists every discrepancy, and exits non-zero on failure. Negative final cash is treated as a likely missing deposit entry; missing or malformed stored ISINs are also reported.

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

Money and end-of-day prices are stored as integer paise. Buys create FIFO lots including buy fees; sells consume those lots atomically, subtract sell fees from proceeds, record realised P&L, and credit only positive sale profit to the Profit Vault. Manually imported unadjusted EQ closes provide as-of market value and unrealised P&L when recent enough; otherwise holdings remain unavailable.

The risk validator receives a portfolio snapshot and never queries the database. Its limits come from validated settings, it returns every rejection reason, and it fails closed when configuration, inputs, snapshot data, or rule execution is invalid.

The frontend is a React/TypeScript dashboard. Its typed client reads the ledger, risk, valuation, and coverage endpoints through Vite’s `/api` proxy. It shows book-cost and dated end-of-day values, “No data” for empty responses, and an error state on API failures; it does not fabricate values.

## Daily ledger-entry routine

Record a deposit, withdrawal, buy or sell **after it actually happened** using the dashboard. Enter rupees with at most two decimal places, whole share quantities, actual fees, and the trade date in Asia/Kolkata. Today is allowed; later dates are rejected. Date-only forms record midnight IST, so entries on the same date follow their submission order for FIFO. Review the exact gross value, fees and total before confirming. The confirmation warns that recorded entries cannot be undone. Recorded events bypass proposed-trade risk validation; a sell beyond held quantity or withdrawal beyond cash is still rejected.

For a buy or sell, the form suggests symbols from stored EQ instruments and shows the latest stored end-of-day close with its date. It warns if the entered price differs by at least 20% or the close is older than the configured staleness limit. Warnings do not prevent recording a genuine event. To change the non-secret warning threshold, set `VITE_PRICE_WARNING_PERCENT` to a whole percent from 1 to 100 in `frontend/.env.local` and restart Vite; see `frontend/.env.example`. The bearer token stays in the root `.env` and is added only by the development proxy.

After confirming, check the refreshed ledger and valuation. If the response is uncertain, the browser retains the exact payload and idempotency key in local storage; reload and use **Retry same submission**. Do not create another entry for the same event. Once the request definitively succeeds or fails, the pending copy is cleared. Pending financial details are stored in that browser profile, so use a private local profile. Download the ledger or per-FIFO-match CSV through the local proxy if needed, and keep exports private. The realised P&L view groups FIFO matches by symbol and supports date or April–March financial-year filters; it makes no tax calculation or tax claim. Run the integrity command and make a backup after recording entries.

## Layout

```text
backend/
  app/api/          HTTP routes
  app/core/         settings, logging, request IDs, exception handling
  app/db/           SQLAlchemy models, engine, and session lifecycle
  app/modules/      portfolio, independent risk, and backtest domain logic
  config/           example backtest config and unverified cost profile
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
backtest-runs/       local trial records (ignored by Git)
```

## Known limitations

- Authentication is a single shared local token, not a user, session, role, or token-rotation system.
- The Vite development proxy provides full API access to anything that can reach port 5173; loopback binding is mandatory.
- Local backups are on the same disk as the database and do not protect against loss of the Mac until copied to a private external drive.
- Backups are not application-encrypted. Protect the Mac, `.env`, backup directory, and external drive with OS-level security.
- Restore must be run with the servers stopped; the CLI does not terminate running application processes.
- Integrity checks detect accounting and SQLite inconsistencies but do not provide cryptographic tamper evidence.
- Price format and `ClsPric` semantics are observed from samples, not verified against an official format specification. The user must confirm the source's terms and closing-price meaning.
- End-of-day prices are unadjusted; returns across splits, bonuses, and dividends must wait for corporate-action handling.
- Prices are manual, EQ-only, and limited by the configurable calendar-day staleness threshold; there are no live quotes or automatic downloads.
- Ticker changes create separate symbol histories; risk capital and risk limits still use FIFO cost, not market value.
- Recorded entries cannot be undone: there is no edit, delete, reversal, or void endpoint. An opposite trade is a new FIFO event and does not unwind an earlier Profit Vault credit.

## Backtesting (Phase 5)

The engine reads daily EQ prices locally and opens an explicitly supplied SQLite file read-only. It runs a separate simulated FIFO ledger in memory; it does not change holdings or prices. The two baselines are buy and hold with a configurable stop below the last close (default 5%) and N-day momentum ranking with top-K periodic rebalance. They are diagnostics, not recommendations. Buy sizes use the previous close and configured risk limits; every proposed fill is checked by the unchanged risk validator at the next open. The constrained equal-weight benchmark needs at least 10 instruments or tranches. The unconstrained equal-weight benchmark has the same costs and slippage but no risk validator; cash is also shown.

Edit a copy of `backend/config/backtest-example.json` for the explicit universe, its as-of date, initial capital, risk limits, volume cap, strategy parameters and optional window size. The placeholder `backend/config/backtest-cost-placeholder-2026-11-02.json` has a broker field, invented nonzero illustrative charges and `verified: false`. It is for invented synthetic data only; its values are not broker rates. A profile with no effective transaction charges is refused even for synthetic runs unless the config explicitly sets `allow_zero_costs: true`; that override prints a warning. Enter broker-verified rates and calculator examples before marking a profile verified; stored-price runs refuse an unverified profile and require positive slippage. Each charge is rounded separately to paise, half up, so totals can differ from a broker by a few paise.

For a synthetic run, supply a local JSON array of invented bars with `symbol`, ISO `day`, integer-paise `open_paise`, `high_paise`, `low_paise`, `close_paise`, `previous_close_paise`, and integer `volume`:

```sh
PYTHONPATH=backend .venv/bin/python -m app.cli backtest \
  --config backend/config/backtest-example.json \
  --cost-profile backend/config/backtest-cost-placeholder-2026-11-02.json \
  --synthetic-bars /path/to/invented-bars.json
```

For stored prices, use a verified profile and an **explicit temporary database copy** with `--database /path/to/copy.db` instead of `--synthetic-bars`. This command does not read `.env`. Run records default to git-ignored `backtest-runs/`; `--runs-directory` can point to a private temporary folder. Each report includes the trial number, data range and number of trading days, rejected reasons, and a banner explaining historical and unadjusted-data limits; it explicitly flags unverified costs on synthetic runs. Annualised metrics are withheld below 252 trading days and warned below 756. The source universe can have survivorship bias, corporate actions are not adjusted, and there is no index benchmark because index data is not stored.

See [ADR 0005](docs/adr/0005-backtesting.md) for event order, stop assumptions, costs, benchmark definitions and limitations.

## Planned extension points

- Corporate-action adjustments and explicit ticker-history mapping after approval
- Additional portfolio and performance reporting
- Independent risk validation before every paper or broker order
- Research, fundamentals, ranking, rotation, advanced evaluation, and paper trading
- Broker integration only after explicit approval and regulatory review

See [ADR 0001](docs/adr/0001-architecture.md) for the original layering decision, [ADR 0002](docs/adr/0002-portfolio-ledger-and-risk.md) for FIFO accounting and fail-closed risk validation, [ADR 0003](docs/adr/0003-hardening.md) for local security and backup decisions, and [ADR 0004](docs/adr/0004-price-data.md) for the observed price profile and its limits.
