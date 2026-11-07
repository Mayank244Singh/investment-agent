# Project Status

## Phase 7D.1: Point-in-time evidence foundation (implemented, uncommitted)

- [x] Integrate committed Phase 7B.1/7B.2A/7B.2B work into local `main`; verify backend, frontend, Ruff and mypy; repair strict typing in a focused Phase 7B commit. No push.
- [x] Create `feature/phase-7d1-evidence-foundation` from verified local `main`.
- [x] Add read-only Indian instrument and typed evidence contracts with exchange, UTC availability/cutoff, provenance, quality, usage eligibility and distinct prediction horizons.
- [x] Validate completed 1/5/15-minute candles against caller-verified exchange sessions and holidays, with duplicate, missing, delayed, stale and future-bar guards.
- [x] Add a deterministic opportunity assessment that preserves supporting and contradicting evidence, withholds trade approval and size, and never calls the production risk gate.
- [x] Add synthetic tests including a future-bar negative control, identity and session failures, evidence disagreement, missing calibration, and no ledger/Profit Vault write path. Review fixes cover off-grid bars, between-close decisions, typed horizons, assessment context, duplicate evidence and explicit wait reasons.
- [x] Verify full backend tests (427 passed, 1 skipped), Ruff and mypy (109 source files) after review fixes.

The assessment is a research contract only. `ACCEPT` and `ACCEPT_WITH_REDUCED_SIZE` are reserved and cannot be emitted until calibration and sizing are reviewed. No real-data training, order, allocation or broker path was added. See `docs/PHASE_7D_1_EVIDENCE_FOUNDATION.md`.

## Phase 7B.2B: Real historical-data readiness assessment (implemented)

- [x] Add a provider-neutral, read-only assessment for 1/5/15-minute intraday and daily swing/long-term data, with explicit calendar/holiday/halt evidence and per-symbol coverage.
- [x] Report all applicable coverage, quality, corporate-action, point-in-time, licensing and provider-access blockers; leave real-data ML training disabled.
- [x] Add a `data-readiness` CLI that opens a selected SQLite database read-only; use only isolated synthetic databases in tests.
- [x] Test missing bars, duplicates, OHLCV/timestamp errors, identity, mixed providers, fail-closed results and source-database immutability.
- [x] Verify the full backend suite (393 passed, 1 skipped), Ruff and mypy (107 source files) after the readiness additions.

This phase implements assessment infrastructure, **not** a usable real-data training dataset. Stored NSE observations are daily, unadjusted and lack verified availability, corporate-action, historical-universe and licensing evidence. No production intraday table or point-in-time fundamental source exists. Upstox historical and LTP access remains blocked by HTTP 403. See `docs/PHASE_7B_2B_REAL_DATA_READINESS.md`.

## Phase 7B.2A: Multi-model runtime verification (complete)

- [x] Inspect the existing Phase 7B.1 path and confirm native macOS arm64 / Python 3.12.16.
- [x] Install approved, pinned ML libraries in `.venv`; record the resolved versions in `backend/requirements-ml.txt`.
- [x] Add a one-time, explicit final-holdout CLI unlock and private atomic claim; test duplicate refusal and symlink protection.
- [x] Verify the previous dependency-free backend baseline (351 passed, 1 skipped), Ruff and mypy (102 source files).
- [x] Train CatBoost regression/classification and scikit-learn Ridge, Elastic Net and Logistic Regression on invented data; check repeat predictions and safe reload.
- [x] Train Random Forest, Extra Trees and HistGradientBoosting in both tasks; persist with pinned `skops` and a fixed tree-type allowlist.
- [x] Train CatBoost, Ridge and Random Forest through the trusted synthetic permit and temporary experiment registry.
- [x] Train and reload LightGBM/XGBoost regression and classification on invented data after installing `libomp` 23.1.5.
- [x] Add an explicit alternating invented fixture for two-class CLI training while preserving the default fixture and its fingerprint; verify four classifier families through the public gate.
- [x] Verify native runtime and CLI paths, full backend suite (381 passed, 1 skipped), Ruff, mypy (103 source files) and `pip check`.

No model trained on real data. Phase 7B.2A verifies synthetic infrastructure and native library operation only; it does not establish real-data model quality or trading readiness. The existing synthetic-only safety gate, ledger, market-data storage, backtester and risk validator remain unchanged. See `docs/PHASE_7B_2A_RUNTIME_VERIFICATION.md`.

Release-candidate verification on 2026-11-06: backend 381 passed, 1 skipped; Ruff clean; mypy clean (103 source files); `pip check` found no broken requirements; frontend 31 passed, ESLint clean and production build passed. This verifies the current local checkout, not a released or real-data-trained system.

## Phase 7B.1: Synthetic-only ML training and evaluation infrastructure

- [x] Inspect Phase 7A research contracts and confirm the 296-pass baseline.
- [x] Keep real-data training disabled; generate deterministic invented fixtures through Phase 7A and validate them again at the ML safety gate.
- [x] Add typed strategy/timeframe/horizon validation, point-in-time label availability, chronological purging, embargo and walk-forward fold contracts.
- [x] Add train-only preprocessing, standard-library baselines, optional LightGBM/XGBoost adapters and explicit undefined evaluation metrics.
- [x] Add a separate Git-ignored, private, atomic experiment registry with duplicate and checksum checks.
- [x] Add synthetic-only validate/train/evaluate/list CLI commands and design documentation; verify focused and full backend tests (349 passed, 1 skipped), Ruff and mypy (102 source files).

At the end of Phase 7B.1 those optional packages were absent. They are now locally installed, and the user installed `libomp` for LightGBM and XGBoost. No real market data is an approved training set.

## Phase 7A: Offline ML research foundation

- [x] Preserve existing daily market-data, ledger, risk and backtest paths while adding typed intraday, swing and long-term research contracts.
- [x] Add synthetic-only 1/5/15-minute candle, calendar, availability, feature, target and chronological-split research paths without a production table migration.
- [x] Add optional daily indicator features with explicit warm-up and OHLC requirements; retain legacy close/volume behavior.
- [x] Add research-only execution and risk assumption contracts; no order or model training path.
- [x] Document formulas, leakage checks, data requirements and known limits in `docs/PHASE_7A_DESIGN.md`.
- [x] Verify full backend regression (296 passed, 1 skipped), Ruff and mypy (93 source files) for this extension.
- [ ] Obtain licensed, long-span daily and intraday histories with verified availability, adjustment and session metadata before any real-data ML work.

At the end of Phase 7A, no intraday provider, corporate-action adjustment, execution simulator, model training or live trading existed. Phase 7B.1 now adds synthetic-only training. Upstox historical and LTP access remains unresolved; later phases require separate approval.

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

- Phase 6A and its real-database migration are complete. Phase 6B.1 finished as an offline mock pilot; Phase 6B.2 attempted bounded live diagnostics but usable historical access remains blocked by HTTP 403.
- Existing baseline strategies and synthetic ML experiments are research-only. No live orders, broker execution, paid APIs or cloud deployment are in scope.
- Dependencies will be project-local and pinned to exact versions.
- Future schema changes require Alembic before the first durable domain model is released; see ADR 0001.
- The frontend reaches the backend through Vite's `/api` proxy, verified manually outside the sandbox.

## Known Issues

- The execution sandbox denies local port binding (`EPERM`) for Uvicorn and Vite. End-to-end proxy verification must be run manually in a local terminal; the user has verified it succeeds there.
- Phase 4's dashboard panels have automated API and UI tests, but a browser check against an imported temporary database is still needed outside the sandbox.
- The bhavcopy profile was observed in five samples, not verified against an official specification; the user must verify `ClsPric` semantics and source terms before real use.
- The opt-in Upstox pilot is not a production-ready source: separately authorized Historical Candle V3 and LTP V3 diagnostics returned HTTP 403. Authorization remains unresolved. Do not automatically retry or make fallback broker requests.

## Next Task

After Phase 7D.1 review, prioritize legally usable historical Indian market data from alternative providers. Verify provider access, retention and ML-training rights, long adjusted histories, corporate actions, historical identity/universe and authoritative exchange calendars before more ML features. The Phase 6 usability browser check, reversal-event design and Phase 5 broker-cost golden example remain pending.

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

## Phase 5: Backtesting engine

- [x] Part A: add read-only SQLite price loading, bounded historical views, and a private in-memory FIFO ledger using the existing ledger service.
- [x] Part A: add a dated, broker-named unverified placeholder delivery-cost profile, per-charge half-up paise rounding, adverse slippage, and a guard against using unverified costs with stored prices.
- [x] Part A: add the daily event loop with next-open fills, unchanged risk validation, simulated India dates, volume reductions, stop and gap handling, corporate-action guards, and stale held-bar flags.
- [x] Part A: test look-ahead prevention, fills, costs, slippage, stops, risk, FIFO, data guards, and unchanged source-database hash; run backend tests, Ruff and mypy.
- [ ] Verify a broker calculator delivery buy/sell golden example after receiving real charge breakdown and rates; the test is pending while the profile remains unverified.
- [x] Audit named Part A tests; no gaps found. Temporarily leak a future bar, observe the no-look-ahead test fail, restore the guard, and observe it pass.
- [x] Add buy-and-hold and N-day momentum baselines, sizing against configured risk limits at the last close and validating actual next-open fills.
- [x] Add labelled constrained, unconstrained and cash benchmarks, metrics, consecutive out-of-sample windows, and a trial-numbered JSON run record in git-ignored `backtest-runs/`.
- [x] Add a local `backtest` CLI, README instructions, ADR 0005, synthetic tests, and final static checks.

### Phase 5 Decisions and limitations

- The universe, its as-of date, initial capital, volume cap, stop percentage and history thresholds are simulation config values. Annualised results will require at least 252 trading days and warn below 756.
- Stops and strategy sells pass through the unchanged risk validator. It checks held quantity on sells and does not apply buy or daily-loss limits.
- The volume cap uses the last completed bar's volume, including on protective sells; unfilled quantity remains open and is reported. A missing held bar prevents execution and marks the last close stale.
- Suspected corporate actions compare stored previous close with the prior stored close. A held flagged instrument always stops the run; an unheld one can be excluded from its flagged date.
- The cost profile contains invented nonzero illustrative charges for synthetic runs, not verified broker rates; it remains `verified: false` and cannot run on stored prices. GST is computed on brokerage, exchange and regulator charges only. Component-level half-up rounding can differ from a broker by a few paise.
- The constrained equal-weight benchmark requires at least 10 instruments or tranches; the unconstrained version bypasses only the risk validator, while retaining cost, slippage, cash and holdings constraints. There is no index benchmark because index data is not stored.
- Every report states the data range, trading-day count, trial number and historical-results warning. Gross return is net plus charges and adverse slippage impact, an attribution estimate rather than a separate cost-free replay.
- Reports and run records explicitly flag an unverified cost profile; the placeholder remains `verified: false`, and the broker golden-example test remains pending until actual dated rates and calculator breakdowns are supplied.
- Runs with no effective transaction charges now require explicit `allow_zero_costs: true` and print a warning; stored-price runs additionally require positive slippage. Synthetic tests cover both refusals and the override.

## Phase 6: Usability

- [x] Add authenticated, read-only realised P&L by symbol and FIFO match, with India-date and April–March financial-year filters and holding periods.
- [x] Add chronological ledger and per-FIFO-match CSV exports, with formula-safe cells and `Cache-Control: no-store`.
- [x] Add a dated latest-EQ-close endpoint for future, non-blocking form warnings; retain the existing symbol-coverage endpoint for suggestions.
- [x] Use Asia/Kolkata calendar dates for manual ledger entry validation: today is allowed and later dates are rejected.
- [x] Test report totals, filters, export escaping, authentication, same-key/different-payload rejection, and the IST midnight boundary on temporary databases.
- [x] Add frontend forms with confirmation, pending idempotency recovery, 20% configurable price warnings, and clear errors; add report and export controls.
- [x] Update the README daily manual-entry routine, private browser-storage guidance, and the no-reversal limitation.

Corrections remain unresolved: the ledger has no edit, delete, reversal, or void endpoint. Opposite trades through the existing POST endpoints are new FIFO events and do not undo earlier Profit Vault credits.

## Phase 6A: Provider-neutral market-data foundation

- [x] Add an additive migration for time-valid instrument mappings, ingestion runs and immutable source observations; retain `Instrument.id`, existing NSE import batches and canonical daily prices.
- [x] Backfill source observations from existing canonical NSE rows on a disposable database. Mark them as reconstructed; keep unknown raw payload, retrieval time and usable-at time null.
- [x] Permit absent source/canonical `previous_close` while preserving NSE-supplied values. Keep the backtest mismatch guard dependent on an independently supplied value.
- [x] Preserve identical observations from independent sources and conflicting revisions without duplicate canonical prices or silent overwrite. Make exact redelivery idempotent and ambiguous identity fail closed.
- [x] Keep the strict offline NSE ZIP importer, canonical valuation, read-only backtester, ledger and risk behavior working; test with synthetic providers and temporary databases only.
- [x] Verify migration row counts, IDs, values, provenance, integrity and safe downgrade behavior on a disposable pre-migration database; run full backend and frontend regressions.

### Phase 6A boundaries and decisions

- ADR 0006 records the distinction between source observations and selected canonical EQ daily bars, source-specific idempotency, stable internal instrument IDs and temporal mappings. No historical ticker relationship is inferred.
- Existing import timestamps are ingestion times, not proven source retrieval or first available-at times. Unknown values stay null; a legacy backfill cannot reconstruct the original CSV payload.
- Existing `ImportBatch.imported_rows` remains a canonical-row count. Extra independent observations require their own provenance/counting without changing Phase 4 import totals.
- No Upstox connection, full-history download, corporate-action adjustment, point-in-time features, ML, AI, intraday ingestion, broker execution, ledger change or risk-rule change belongs to Phase 6A.
- The real `data/` database was migrated to `0004_market_data_foundation` on 3 November 2026 after a verified backup and a successful full-data temporary rehearsal; its legacy values, counts and integrity were unchanged.

## Phase 6B.1: Offline Upstox historical-data pilot

- [x] Add an isolated Historical Candle V3 adapter with injectable HTTP transport, bounded timeouts and retries, and runtime-only Analytics Token loading for future explicit live mode.
- [x] Validate five synthetic mock instrument-master records against stable internal IDs and ISINs; reject unknown or ambiguous mappings.
- [x] Parse and store synthetic daily OHLCV observations in integer paise on a temporary database only, with nullable source previous close and no canonical overwrite.
- [x] Reconcile the five pilot symbols across their common 26–30 October 2026 NSE dates (25 reference observations), reporting exact OHLCV matches, differences, missing sessions and revisions.
- [x] Run mocked adapter, mapping, idempotency, security and reconciliation tests plus the existing backend/frontend checks; document the offline CLI and limitations.

### Phase 6B.1 boundaries

- Offline mock mode requires no Upstox token and makes no provider request. Mock keys, metadata and candles are synthetic evidence for the code path, not verified real Upstox data.
- During Phase 6B.1 the CLI refused live mode even if `--allow-live` and a token were present. No Upstox observations were written to the real database.
- At the end of Phase 6B.1 official API semantics, instrument identity, data-use terms and real NSE reconciliation were still unverified. A mock comparison cannot approve Upstox for a larger historical download.
- The offline five-stock run used 25 real NSE reference bars and synthetic mock responses: 22 exact matches, one one-paisa close difference, one one-unit volume difference and one missing mock session. These discrepancies were deliberately injected. Identical redelivery created zero new observations, and the real database SHA-256 was unchanged. Full checks: backend 246 passed, 1 skipped; frontend 31 passed; Ruff, mypy (77 files), frontend lint and build passed.

## Phase 6B.2: Bounded live Upstox pilot

- [x] Verify the official Historical Candle V3 endpoint, daily response, instrument master, Analytics Token scope and rate-limit documentation.
- [x] Verify one official NSE EQ master match, including ISIN and provider key, for each of the five pilot symbols.
- [x] Enable a five-symbol, five-request maximum live CLI using a private token, fresh temporary database, single-attempt requests and no redirect following.
- [ ] Obtain one usable real candle response and reconcile all five symbols. **Blocked:** the first authenticated historical GET had an indistinguishable HTTP 401/403 status in that run; later separately authorized Historical Candle V3 and LTP V3 diagnostics each returned HTTP 403. No usable candle was obtained.
- [x] Record the blocked attempt, zero usable Upstox bars, source-database hash check and unresolved data rights in `docs/reports/UPSTOX_PHASE_6B2_PILOT.md`.
