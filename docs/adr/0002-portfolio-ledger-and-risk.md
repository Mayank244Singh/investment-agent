# ADR 0002: FIFO portfolio ledger and fail-closed risk validation

- Status: Accepted
- Date: 2026-11-01

## Context

Phase 2 requires manually entered portfolio accounting and independent validation of proposed trades without market-data, prediction, strategy, broker, or execution capabilities. Results must remain auditable and money must never use binary floating point.

## Decision: portfolio ledger

The ledger is append-only. Deposits, withdrawals, buys, and sells are immutable; corrections will use reversing entries rather than edit or delete operations. Money is stored as integer paise and cash-equity quantities are positive whole integers. Persisted event timestamps are UTC.

Cost basis uses FIFO. Every buy creates a lot whose total cost includes buy fees. A sell consumes the oldest open lots first, allocates exact integer-paise cost and sell fees across matched lots, subtracts fees from proceeds, and records realised P&L. Rounding residue remains with the final portion so the stored allocations reconcile exactly.

Lot matching, realisations, lot updates, and any Profit Vault entry are one database transaction. A sale credits the vault only when its aggregate net realised P&L is positive. Later losses do not reduce prior vault credits. Deployable cash is cash minus the vault; risk capital is deployable cash plus open lots at cost.

Manual ledger entries record events that already happened, so they bypass the risk validator by design. The write API rejects malformed events and impossible accounting transitions—withdrawals above the recorded cash balance and sells above held quantity—but does not retroactively apply proposed-trade risk limits. Optional client-supplied idempotency keys are persisted with immutable movements and transactions so an exact replay returns the original record without duplicating ledger effects.

## Decision: risk validation

The risk validator is a pure domain component. It receives a `ProposedTrade`, a complete portfolio snapshot, and limits copied from validated application settings. It never queries the database and imports no forecasting, AI, broker, or execution module.

The validator checks deployable cash, maximum order size, maximum post-trade single-stock allocation, maximum stop-loss exposure, maximum daily realised loss, buy stop-loss requirements, quantity and price sanity, held quantity for sells, and the no-short-selling rule. It returns every detected rejection reason.

Validation fails closed: missing or invalid settings, proposal fields, snapshot fields, timestamps, or rule execution cause rejection. Defaults are centralized in configuration and may be overridden by environment variables: 10% maximum order, 20% maximum stock allocation, 1% maximum loss per trade, and 2% maximum daily realised loss. Configuration rejects percentages at or below zero or above 100% during startup.

Daily loss uses realised events only and resets at midnight `Asia/Kolkata`; timestamps remain UTC. Stop-loss is mandatory for buys, positive, and below entry price. Sells require sufficient held quantity and do not require a stop-loss.

## Consequences and known limitations

- There is no unrealised P&L or market-value equity because Phase 2 has no verified market prices.
- Daily loss is realised-only; it does not include open-position drawdown.
- Position allocation and risk capital use book cost, not current market value.
- FIFO and integer-paise allocation are application accounting choices, not tax advice.
- The Profit Vault is a virtual reserve, not a separate bank or broker account.
- Withdrawals are limited by total cash, not deployable cash, so a withdrawal can reduce cash below the Profit Vault balance.
- Validation is advisory only: no order object is persisted or executed.
- The local API has no authentication yet and must remain bound to `127.0.0.1`.
