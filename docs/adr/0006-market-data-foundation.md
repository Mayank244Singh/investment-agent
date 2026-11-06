# ADR 0006: Source observations and canonical daily prices

- Status: Accepted for Phase 6A; implemented and verified on disposable databases
- Date: 2026-11-03

## Context

ADR 0004 established a strict, local NSE ZIP importer and one canonical unadjusted EQ price per instrument/date. Before Phase 6A, that importer skipped a matching second row and rejected a conflicting one, so independent evidence was not retained. `DailyPrice.previous_close_paise` was required even though a future provider may not supply it. The existing `Instrument.id` is already a stable database identifier, while symbol/series still act as the NSE ZIP import lookup key. Phase 6A establishes only the boundary needed before a second provider or point-in-time research; it does not connect a provider.

## Decision

Keep `Instrument.id` as the internal identity and existing `ImportBatch` rows as the provenance for historical NSE files. Add time-valid instrument mappings that associate an internal instrument with a source/provider key, exchange, symbol and series. Unknown validity dates remain null; they are not treated as evidence that a ticker relationship existed forever. No legacy ticker histories are guessed or merged. Resolution of a new observation must produce one supported internal identity; ambiguity or an ISIN conflict fails closed.

Add a provider-neutral ingestion run and an immutable source-observation record. Its initial reconciliation classification is a recorded result at insertion time; a later resolution would require a separate append-only decision/event rather than updating the observation. A source observation carries the source, provider instrument key where known, session date, supplied OHLCV and optional source previous close, original payload identity/hash, first ingestion time and its run. Event/session date, provider retrieval time, local ingestion time and usable/available-at time are distinct. Retrieval and availability are null when unknown; an import timestamp is not silently promoted to either. The original supplied payload is retained for new observations where available. A legacy observation reconstructed from the existing canonical row is explicitly marked as reconstructed, with unavailable raw payload and unknown source retrieval/availability left null.

Observation identity is source-specific, not canonical-bar identity. An exact repeated delivery of the same source record and payload hash is idempotent. Another provider's identical OHLCV is a separate observation. A changed payload for the same logical source record is a new revision and does not mutate the earlier record. For an NSE ZIP, the archive identity and CSV row location distinguish source records; for a future API, the provider's instrument key/session and any documented revision identity will determine the logical key. This ADR does not authorize inventing a provider-specific key before that provider is tested.

`DailyPrice` remains the selected, normalized **canonical** daily EQ bar consumed by valuation and the existing backtester. It retains one bar per instrument/trading date and never averages providers or overwrites a conflict. A selected source-observation link records why a canonical bar exists. Existing NSE batch linkage remains queryable; a future non-file source need not fabricate an `ImportBatch`. The source observation and canonical bar may therefore share normalized values but have different identities and retention rules. Conflict/reconciliation policy remains a separate future decision; this phase does not choose a provider priority.

Make `previous_close_paise` optional in the general canonical/source contract. The NSE ZIP's `PrvsClsgPric` remains stored as supplied. A missing field from another provider stays unknown. No previous close is fabricated. The existing backtest corporate-action mismatch heuristic runs only when an independently supplied previous close exists; deriving it from the very preceding close under comparison would make the check tautological. Stored-price backtests currently refuse a dataset containing unknown previous closes until that quality guard is independently resolved; synthetic bars may omit them. Corporate-action adjustments and a derived research previous close belong to a later phase.

New prices are normalized with decimal-safe parsing to integer paise and validated before canonical selection. The strict ZIP name/member/header/date/row rules, EQ-only scope, atomic import, archive-hash no-op, ISIN conflict stop and canonical conflict refusal remain. Source observations for a valid ZIP file and its canonical changes should commit together. A same-price observation from another source is retained even when no additional canonical bar is created. `ImportBatch.imported_rows` continues to mean newly linked canonical prices; any additional observation counts are reported separately.

## Migration and backfill

Use an additive Alembic migration. Existing `instruments`, `daily_prices` and `import_batches` rows and IDs remain intact. Backfill one reconstructed observation for each existing canonical NSE price, linked to its original instrument and import batch/run. Copy stored numeric fields exactly; do not claim to reconstruct the original CSV row, source publication time, provider API key, download time or first available-at time. Keep the canonical row's prior price and batch links. Verify row counts and each canonical value on a disposable database before suggesting a real migration.

An upgrade or downgrade is tested only on a temporary database. A downgrade must not silently discard newly acquired provider observations or canonical rows that cannot fit the old non-null batch/previous-close schema; it should refuse such a lossy operation. The user must make a verified backup and stop running servers before any later migration of the real database. This task does not run migrations on `data/`.

## Consequences and deferred work

- Re-import of the exact same archive remains a no-op; a different source's matching or conflicting observation can be audited separately without changing canonical valuation.
- Mapping validity and source availability may be unknown. Unknown is represented as unknown, and ambiguous identity prevents new canonical selection.
- The current valuation and daily backtester continue to read canonical bars. They do not interpret arbitrary source observations.
- A source revision can later trigger canonical re-evaluation and versioned research-dataset invalidation while retaining the originally seen observation and decision audit. No invalidation engine exists in Phase 6A.
- Upstox integration, automatic downloads, corporate actions, adjusted prices, features, ML, AI, intraday data and broker execution are **not implemented** by this ADR.
- The separate `BACKEND_HOST` setting and Uvicorn bind address remain local-only technical debt to resolve before any network exposure; this market-data change does not alter security binding.
