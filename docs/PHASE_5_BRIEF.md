# Phase 5: Backtesting engine

## Goal
A deterministic, honest backtesting engine for daily bars that replays
stored prices through the real risk validator, with Indian trading costs,
slippage and benchmark comparison. No ML, no LLM, no broker, no live data,
no orders. Only simple baseline strategies, never presented as
recommendations.

## Scope
1. Read-only data access over the Phase 4 price tables. Trading days are the
   dates present in the data. The engine never writes to prices.
2. No look-ahead by construction: a strategy receives a data view that
   contains only bars up to and including the decision date. Orders decided
   after day t's close are filled at day t+1's open. Add a test that fails
   if a strategy can read a later bar.
3. Isolated simulated portfolio: reuse the existing FIFO and cost-basis
   logic against an in-memory or temporary database. A backtest must NEVER
   write to the real ledger or the real database.
4. Every proposed trade goes through the real risk validator with a
   portfolio snapshot, using simulated dates (never the wall clock).
   Rejected trades are logged with reasons and counted. Stop-losses are
   simulated: if a day's low is at or below the stop, exit at the stop, or
   at the open if it gapped below the stop. Assume the stop is hit when the
   low touches it (conservative).
5. Costs: a configurable cost profile with a rates_as_of date and a source
   note, covering brokerage, statutory taxes and charges, exchange and
   regulator charges, stamp duty, GST and depository charges, separately for
   delivery trades. No rates are hardcoded in code; defaults in tests are
   round synthetic numbers, labelled as such. Slippage is configurable in
   basis points. Optional cap on order size as a fraction of the day's
   volume; orders above the cap are reduced or rejected and reported.
6. Data-quality guards, fail closed by default:
   - suspected corporate action: flag any instrument-day where the stored
     previous close differs from the prior stored close, and exclude that
     instrument from the run or stop, as configured
   - missing bar for a held instrument: no trade that day; mark to the last
     close and flag as stale
   - insufficient history: below a configurable minimum number of trading
     days, refuse to report annualised metrics and print a clear warning
   - survivorship warning: state that the universe is whatever instruments
     exist in the data
7. Metrics, with money kept in integer paise: gross and net return, total
   costs and cost drag, max drawdown, volatility, number of trades,
   turnover, exposure, rejected-trade count, final Profit Vault balance.
   Ratios may use floats. Annualised figures only when history allows.
8. Benchmarks: buy and hold of the same universe (equal weight, same costs)
   and cash. An index benchmark needs index data, which is not stored yet;
   state this limitation in the output and the ADR.
9. Strategy interface plus two baselines only: buy and hold, and a simple
   N-day momentum ranking with a top-K hold and periodic rebalance. They
   exist to show what normal looks like and to test the engine.
10. Windowed evaluation: a harness for running the same configuration over
    several consecutive out-of-sample windows.
11. Reproducibility and trial log: each run saves a JSON record in a
    git-ignored directory with the config hash, data coverage, code version
    if available, and results. Keep a trial counter per strategy family and
    print it, so selection bias is visible.
12. CLI to run a backtest; ADR 0005; README section; known limitations.

## Rules
- Money is integer paise, never floats. Business logic outside the CLI.
- Prefer no new dependencies (standard library only).
- The risk module is used as-is and must not be modified or bypassed.
- Tests use synthetic price data with invented symbols. No real exchange
  rows in the repository.
- Tests for: no look-ahead, next-open fills, each cost component, slippage,
  stop-loss and gap-through-stop, risk rejections, FIFO consistency with the
  ledger, the real database is untouched, corporate-action flag, stale and
  missing bars, insufficient history refusal, determinism (same inputs give
  identical results), benchmark correctness on a hand-calculated example.
- Never present results as predictions or recommendations.
- Do not commit or push. I do that myself.
- Update docs/PROJECT_STATUS.md after each step.
