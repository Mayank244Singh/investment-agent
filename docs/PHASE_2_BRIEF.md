# Phase 2: Portfolio ledger and risk validator

## Goal
Build the portfolio ledger and an independent risk validator. Pure domain
logic plus persistence and a small API. No market data, no prediction, no
strategies, no broker, no Groww, no paid APIs, no order placement.

## Scope
1. Alembic migrations, with the first tables.
2. Portfolio ledger (manually entered data only):
   - capital deposits/withdrawals
   - holdings and transactions (buy/sell, symbol, quantity, price, date)
   - cost basis and realised P&L (record the cost method in the ADR)
   - Profit Vault: realised profits tracked separately from original capital
   - NO unrealised P&L yet, because it needs market prices
3. Risk validator (modules/risk):
   - input: a ProposedTrade object (a data object, not an order)
   - output: approved or rejected, with every reason listed
   - rules, with limits read from config and NEVER hardcoded:
     sufficient cash, max position size as % of capital, max single-stock
     allocation, max loss per trade, max daily loss, stop-loss required,
     quantity and price sanity checks
   - fail closed: if any input or rule is missing or errors, reject
4. API: endpoints to view the ledger and to submit a ProposedTrade for
   validation only. Nothing is executed.
5. Frontend: show ledger and risk-rule status with real data from the
   backend. If there is no data, show "No data". No fabricated numbers.

## Rules
- Money uses Decimal or integer paise, never float.
- Risk module must not import from forecasting, AI or broker code.
- Business logic stays out of routes and UI components.
- Tests for every risk rule, including rejection and fail-closed cases.
- Record decisions in docs/adr/ and progress in docs/PROJECT_STATUS.md.
- Do not commit or push.
