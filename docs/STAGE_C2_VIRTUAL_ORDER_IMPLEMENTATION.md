# Stage C2 — isolated, risk-gated virtual orders

This stage adds a **synthetic demo** to the existing application. It is not market data, a live order system, advice, or an execution simulator. No FYERS/NSE request, canonical daily-price write, provisional-candle read, production-DB migration, or broker operation is part of the workflow.

## Architecture and isolation

- `VIRTUAL_DATABASE_URL` is optional and must point to a pre-existing **absolute SQLite file** different from `DATABASE_URL`. The virtual API refuses an unseeded database, the standard production filename, or known pilot/provisional filenames and paths. The virtual DB marker records INR, ₹10,000 opening capital, and a deterministic fixture hash. Seed is explicit and idempotent; it refuses a nonempty, unmarked database.
- Alembic `0005_virtual_orders` adds only `virtual_accounts` and `virtual_order_attempts`. Apply this migration to the **isolated virtual file**, not the production DB. Existing ledger tables remain the single source for cash, FIFO lots, realised P&L and Profit Vault. The virtual order table records decisions, source quote, costs and idempotency linkage; it is not a second portfolio ledger.
- Five synthetic quote fixtures use the display symbols RELIANCE, TCS, INFY, HDFCBANK and ICICIBANK, with deliberately invented prices. They live in application code, are generated for the current Asia/Kolkata date, and have an explicit `SYNTHETIC DEMO — NOT MARKET DATA` label and stable price-fixture hash. They are never inserted into `daily_prices`, `source_observations`, `pilot.db`, `provisional.db`, or a research dataset. The virtual account refuses any canonical price or source-observation row. The only route to a virtual fill is the authenticated `/api/v1/virtual/orders` endpoint.
- The existing historical-entry `/api/v1/portfolio/*` routes continue using the separate configured `DATABASE_URL`. Their buys and sells still record completed events without pre-trade risk checks; the dashboard labels that form accordingly. They cannot write to the separately configured virtual DB. The Vite proxy still holds the bearer token server-side.

## Order flow and risk

`POST /api/v1/virtual/orders/preview` is read-only. Submission acquires a SQLite immediate write transaction, checks an idempotency key, reads current virtual cash/lots and the current synthetic quote, calculates adverse slippage and the existing **unverified placeholder** delivery charge profile, calls the independent `RiskValidator`, then applies fixed virtual paise caps. A rejected order writes an audit attempt but no fill. An approved order calls `LedgerService.record_buy` or `record_sell` with an outer-transaction option, then commits the ledger changes and attempt **atomically**. Existing historical ledger callers retain their original auto-commit behavior. Submissions recompute the decision, even after a successful preview. Concurrent submissions serialize before reading the snapshot.

The original validator uses risk capital = cash + open FIFO cost − Profit Vault; its default percentages are 10% maximum order, 20% single-stock allocation, 1% stop-based trade exposure and 2% daily net realised loss. For the virtual account, extra fixed caps of **5,000 paise planned loss**, **10,000 paise daily net realised loss**, and **200,000 paise post-buy open FIFO cost per symbol** apply. The stricter of percentage and fixed caps wins. Planned virtual loss includes adverse buy fill, a further adverse hypothetical exit at the stop, estimated buy/sell fees and whole-share quantity. It is an estimate, **not a guaranteed loss limit**; a gap or failed stop can exceed it. The stop is a planning input and is not automatically executed. Sells are limited by held quantity, and daily/trade loss limits do not block exits. All financial amounts use integer paise or Decimal and component-level half-up rounding. The cost profile is illustrative and not broker-verified.

## API and UI

All virtual routes are bearer-protected:

| Route | Purpose |
| --- | --- |
| `GET /api/v1/virtual/account` | Virtual cash, deployable cash, realised P&L, Profit Vault, FIFO holdings, and dated synthetic market value/unrealised P&L. |
| `GET /api/v1/virtual/quotes` | Five current synthetic reference quotes with dates, provenance and fixture hash. |
| `POST /api/v1/virtual/orders/preview` | Validate a market-style buy/sell with whole quantity and a required buy stop; return all rejection reasons and illustrative costs without writing. |
| `POST /api/v1/virtual/orders` | Require `Idempotency-Key`, revalidate under a transaction, return a filled or rejected attempt and updated account. A reused key with a different payload returns 409. |
| `GET /api/v1/virtual/orders` | Durable virtual attempt history, including accepted fills and rejected reasons. |

The dashboard has a separate **Virtual trading demo** panel with a synthetic disclosure, account summary, holdings, ticket, preview/confirmation, risk reasons, filled/rejected history and reload-safe retry of an uncertain submission using the same key. The existing historical-entry form remains visibly distinct.

## Local demonstration (after review)

These commands intentionally address a disposable virtual file. Run them from this repository worktree, with the existing project virtual environment; do **not** use a production/pilot/provisional path. They do not require a broker token or network. The backend and Vite proxy need the **same** local bearer token. If this worktree has no `.env`, create a fresh, private throwaway one using the README generator; do not copy or print a production credential. Set `AUTH_TOKEN` there and run `chmod 600 .env` before starting either server. If `.env` already exists, do not overwrite it.

```bash
cd /Users/mayanksingh/.codex/worktrees/phase-7b-integration/investment-agent
export VIRTUAL_DB_PATH="$PWD/data/virtual_mvp.db"
export PROJECT_PYTHON=/Users/mayanksingh/Investment/investment-agent/.venv/bin/python
export PROJECT_ALEMBIC=/Users/mayanksingh/Investment/investment-agent/.venv/bin/alembic
cd backend
DATABASE_URL="sqlite:///$VIRTUAL_DB_PATH" "$PROJECT_ALEMBIC" upgrade head
cd ..
PYTHONPATH=backend "$PROJECT_PYTHON" -m app.virtual_cli --database "$VIRTUAL_DB_PATH" --production-database "$PWD/data/investment_agent.db"
PYTHONPATH=backend VIRTUAL_DATABASE_URL="sqlite:///$VIRTUAL_DB_PATH" "$PROJECT_PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second local terminal, run `cd /Users/mayanksingh/.codex/worktrees/phase-7b-integration/investment-agent/frontend && npm run dev -- --host 127.0.0.1 --port 5173`. Open `http://127.0.0.1:5173`. Verify cash ₹10,000, synthetic disclosure, no holdings and five dated fictional quotes. Preview one share of INFY with a ₹79 stop; confirm and verify one fill, lower cash and FIFO holding. Preview an overlarge buy and an uncovered sell; both should show rejection reasons and no new ledger fill. Sell the held share, then check realised P&L, Profit Vault behavior and order history. Repeating an order with the same idempotency key must return the same attempt. The virtual file is git-ignored. Stop both servers after the demonstration.

## Verification and limitations

Tests use temporary SQLite databases and synthetic fixtures. They cover seed replay, authenticated API, migration upgrade/downgrade, preview immutability, successful and rejected orders, stock/risk/cash limits, insufficient holdings, quote staleness, IST daily-loss reset, idempotency, concurrency, rollback, fees/slippage and Profit Vault. The browser tests cover synthetic labeling, dated prices, preview, rejection, confirmation, retry and unavailable account state.

Limitations: the current-date fixture is generated for a demo and does not model an exchange session or market movement; the synthetic quote itself is a scenario, not an observed tradable price. Stop orders are **not** monitored or executed. Charges and slippage are illustrative; no spread, liquidity, partial fill, latency, corporate action, tax treatment or broker fee agreement is modeled. Existing manual ledger corrections remain unavailable. Real-data readiness and all 95 PROVISIONAL FYERS candles remain separate and unchanged.
