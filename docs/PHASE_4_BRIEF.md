# Phase 4: Historical end-of-day price storage (manual import)

## Goal
Store verified end-of-day prices and show market value and unrealised P&L
for existing holdings. No prediction, strategies, broker, Groww, paid APIs,
network downloads or orders.

## Data source rules
- The application NEVER downloads data and makes no network calls for
  prices. The user downloads files manually from a source whose terms the
  user has read. Record the source name and file name with every import.
- No scraping and no third-party downloader libraries.
- Test fixtures are small synthetic files, clearly labelled as fake. Never
  commit real exchange data to the repository.

## Scope
1. Tables and migration: instruments (symbol, series, ISIN optional),
   daily_prices (instrument, trade date, open, high, low, close,
   previous close, volume), import_batches (file name, SHA-256 of the file,
   source label, row counts, status, imported-at in UTC).
2. All prices stored as integer paise, parsed with Decimal. Reject more than
   two decimal places unless the plan proposes a safe alternative.
3. Importer CLI for local CSV files:
   - strict column check: refuse a file whose columns do not match the
     expected format, and name the missing or unexpected columns
   - row validation: positive prices, high >= low, open and close within
     [low, high], non-negative volume, valid date, no duplicates
   - reject bad rows with line numbers and reasons; the user chooses
     all-or-nothing (default) or skip-bad-rows with a report
   - atomic: one transaction per file; re-importing the same file hash is
     a no-op; a conflicting price for the same symbol and date is reported,
     never silently overwritten
   - equity series filter configurable (for example EQ only)
4. Prices are UNADJUSTED for splits, bonuses and dividends. Record this in
   the ADR and in the data model. Prices must not be used to compute
   returns across such events until corporate actions are handled later.
5. Portfolio: market value and unrealised P&L per holding using the latest
   close on or before an as-of date. If there is no price, or it is older
   than a configurable staleness limit, show "unavailable". Never guess or
   forward-fill silently. Always show the price date.
6. The risk validator rules do NOT change in this phase. Moving risk
   capital from cost to market value is a decision for a later phase.
7. API (authenticated) for price coverage and holdings valuation; frontend
   shows coverage (latest date per symbol), market value, unrealised P&L
   and "unavailable" states.
8. Integrity check extension for price data; update README, ADR 0004 and
   known limitations.

## Rules
- Prices are integer paise, never floats.
- Business logic outside routes and UI.
- Tests: valid import, wrong columns, each bad-row rule, duplicate file,
  conflicting price, rollback on failure, stale and missing price shown as
  unavailable, importer makes no network calls.
- Do not commit or push. I do that myself.
- Update docs/PROJECT_STATUS.md after each step.
