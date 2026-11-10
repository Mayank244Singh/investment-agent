# Project Status

## Current project status — PAUSED (11 November 2026)

The owner is prioritizing learning financial markets, trading strategies and
risk management before continuing development. The existing application,
research code and isolated pilot evidence are preserved. This is a local
checkpoint on branch `feature/phase-7d1-evidence-foundation`; it is not a
production release, and it is not merged or pushed. Historical entries below
record what was true at each earlier milestone; this section is the current
handoff.

### Completed functionality at the pause

- FastAPI backend with settings, bearer-token protection, request-ID logging,
  health checks and authenticated read/write APIs; React/Vite dashboard with
  ledger forms, portfolio reports, price coverage and a separately labeled
  virtual trading panel.
- SQLAlchemy/SQLite models and Alembic migrations; append-only manual ledger,
  FIFO cost basis, realised P&L, Profit Vault, valuation and read-only integrity
  check. The independent deterministic risk validator checks proposals; manual
  past-event ledger entries do not pass through it.
- Isolated ₹10,000 **synthetic** virtual account with risk-gated simulated
  orders, idempotency, FIFO ledger reuse and an audit trail. It does not send
  broker orders or use provisional FYERS candles.
- Strict local NSE UDiFF ZIP import, daily price storage and provenance,
  read-only reference validation, and 19-session NSE/FYERS pilot
  reconciliation. The existing 95 FYERS daily pilot observations are staged
  only as PROVISIONAL in private storage.
- Read-only FYERS Historical API adapter, offline-validated provisional
  staging and resumable five-year collector with reviewed NSE session calendar,
  immutable checkpoints and isolated paths. Its five-year live acquisition is
  **blocked** as described below. Upstox access also remains unresolved.
- Daily-bar backtesting, costs, slippage, benchmark and walk-forward research
  infrastructure; a reproducible **synthetic** 10/30-session trend baseline.
  Phase 7A/7B research contracts and nine ML library model families were
  runtime-tested on synthetic data only. Point-in-time evidence assessment
  does not promote models, size positions or authorize trades.
- Existing CLI commands include `backup`, `restore`, `integrity`,
  `import-prices`, `backtest`, `data-readiness`, `check-fyers-history`,
  `import-fyers-daily`, `stage-fyers-daily`, `collect-fyers-history`,
  `validate-nse-references` and virtual trading tools. Use `--help` before
  invoking a data-writing or live command.

### Unresolved blocker — FYERS HTTP 422

| Request | Symbol / parameters | Result |
| --- | --- | --- |
| Original first acquisition window | `NSE:RELIANCE-EQ`, `resolution=D`, `date_format=1`, `range_from=2021-11-10`, `range_to=2022-11-10` | HTTP 422; FYERS code `-50` (sanitized: invalid request parameter); zero completed windows |
| Diagnostic 1 | Same symbol, single day `2021-11-10` | HTTP 422; code `-50` |
| Diagnostic 2 | Same symbol, single day `2024-11-11` | HTTP 200; one complete RELIANCE candle for that date |

The newer-date response demonstrates that authentication and the request
format worked for that request. Because the old single-day request also
failed, the original 366-day window length is not the sole cause. A
historical retention or entitlement boundary is suspected, **not confirmed**.
The precise cutoff is unknown, and the provider's specific error message
was not retained. Do not claim the collector is repaired or retry the live
acquisition without a new bounded approval.

### Data integrity and eligibility

The canonical database remained unchanged during the diagnostics. The
five-year collector completed no acquisition window. The isolated pilot
database, provisional database, private raw checkpoints, reviewed calendar
and existing NSE reference ZIPs remain preserved outside Git. Historical
FYERS ticker–ISIN identity is still PROVISIONAL; corporate-action adjustment
equivalence and point-in-time availability are not verified. Those
observations remain ineligible for canonical research, ML training,
backtests and portfolio or trading decisions.

### Development restart instructions

1. Open `/Users/mayanksingh/.codex/worktrees/phase-7b-integration/investment-agent`
   on `feature/phase-7d1-evidence-foundation`. Inspect `git status` and the
   last local checkpoint commit before doing anything else. Do not touch the
   production database or the synthetic virtual account for FYERS research.
2. Use Python 3.12. The worktree runtime is `.venv312/bin/python`; the
   current developer tools are available from the main checkout's `.venv`.
   For the verified backend test/lint/type commands, from the worktree root:

   ```bash
   export PYTHONPATH=/Users/mayanksingh/Investment/investment-agent/.venv/lib/python3.12/site-packages:backend
   PYTHONDONTWRITEBYTECODE=1 .venv312/bin/python -m pytest -p no:cacheprovider backend -q
   .venv312/bin/python -m ruff check --no-cache backend
   .venv312/bin/python -m mypy --no-incremental --no-sqlite-cache --cache-dir /dev/null backend/app backend/tests
   ```

3. Frontend commands from `frontend/` are `npm test`, `npm run lint` and
   `npm run build`. Dependencies must be local and match `package-lock.json`.
   The pause verification used a copy of the main checkout's matching
   `node_modules`; it was not staged.
4. FYERS App ID is read from private `~/fyers-auth/.env` as `FYERS_APP_ID`,
   and the access token from private `~/fyers-auth/.access_token`. Keep both
   outside Git, check permissions/freshness without displaying values, and
   require explicit approval for each live diagnostic. The isolated pilot
   root is `~/InvestmentAgentData/pilots/fyers-b1/` (`pilot.db`,
   `provisional.db`, `raw/`, private calendar and full symbol master). Use
   `PYTHONPATH=backend .venv312/bin/python -m app.cli --help` and the
   subcommand `--help` screens to inspect current syntax. Never invoke
   `collect-fyers-history` live by default.
5. Important source files: `backend/app/services/fyers_historical_provider.py`,
   `fyers_history_collector.py`, `fyers_history_cli.py`,
   `fyers_provisional_service.py`, `nse_reference_validator.py`,
   `backend/app/modules/research/evidence.py`,
   `backend/app/modules/backtest/trend_research.py`,
   `backend/app/services/virtual_trading_service.py`, and `backend/app/cli.py`.
   The collector design and calendar evidence are in
   `docs/STAGE_D2_5_FYERS_HISTORY_COLLECTOR.md` and
   `docs/STAGE_D2_6_NSE_TRADING_CALENDAR.md`.

### Future investigation, only after development resumes

1. Recheck current FYERS documentation and account entitlements; confirm the
   exact supported historical cutoff with the provider.
2. Improve sanitized FYERS error reporting so a specific safe provider code
   and message category survive without leaking secrets or response bodies.
3. Review date-range planning and provider window limits; change the
   collector only after the cause is established.
4. Add offline regression tests, perform one bounded live verification with
   explicit approval, then resume historical acquisition only if validated.
5. Independently resolve historical identity, corporate actions, point-in-time
   availability and data-use eligibility before real-data ML or trading use.

Pause verification: backend **565 passed, 1 skipped**; Ruff passed; mypy
passed on **126 source files**; frontend **36 passed**, ESLint passed and
production build passed. The normal frontend `dist/` build was blocked by
local sandbox permissions; the same build passed with output under
`/private/tmp`. No live API request or production database write was made
during this checkpoint.

## Stage D2.6: five-year NSE equity trading calendar

- [x] Review NSE Capital Market holiday notices for 2021–2026, later holiday
  amendments, ten live special sessions, and the published normal trading rule.
- [x] Preserve the existing 19 reviewed sessions; create an explicit private
  1,826-date calendar for 10 November 2021–9 November 2026 (1,241 sessions,
  585 non-trading dates). All 19 local NSE ZIP dates remain sessions.
- [x] Tighten the five-year collector preflight to require classification of
  weekends as well as weekdays; add offline regression tests.
- [x] Obtain a passing full collector preflight: use the full private FYERS
  symbol master whose SHA-256 already matches the identity manifest, rather
  than the five-row subset. Result: `preflight: PASS`, 25 windows, 95 NSE
  reference rows, zero credential reads or database writes.

See `docs/STAGE_D2_6_NSE_TRADING_CALENDAR.md`. No FYERS request, credential
read, database write, canonical promotion or real-data training occurred.

## Stage D2.5: provisional five-year FYERS daily collector (offline implementation)

- [x] Reuse the FYERS adapter, provisional SQLite/raw checkpoints, strict NSE
  reference reconciliation and immutable canonical read path; add inclusive
  366-day planning, bounded retry/request budgets, private snapshot, resume,
  progress and reporting. No authentication helper, ledger, risk, virtual
  account, strategy or ML gate was changed.
- [x] Add a five-stock example universe with current ticker/ISIN evidence
  references and synthetic offline tests. Every candle stays PROVISIONAL;
  historical mapping, adjustment and availability remain unverified.
- [x] Run offline five-year preflight with zero requests and writes: **BLOCKED**
  at 2021-11-10 because the reviewed calendar covers only 19 sessions in
  October–November 2026.
- [x] Obtain a reviewed full-period NSE session/holiday/special-session
  calendar and pass offline preflight with the matching full FYERS master.
  Live collection still needs separate approval. Do not train, backtest on
  provisional data or expand to 300–500 stocks.

See `docs/STAGE_D2_5_FYERS_HISTORY_COLLECTOR.md`. FYERS Standard's rolling
five-year boundary may reduce the oldest available dates before acquisition.

## Stage D1: synthetic quantitative research baseline (implemented locally)

- [x] Reuse bounded backtest history, next-open simulation, independent risk gate, FIFO ledger, cost model and metrics for a 10/30-session moving-average trend baseline.
- [x] Run a fixed 100-session invented daily scenario with 30 warm-up sessions, and compare against risk-capped buy-and-hold and cash over the same 70-session window.
- [x] Save deterministic JSON and Markdown reports with configuration/data hashes, equity curves, fills, costs and the explicit synthetic-data warning; document the method in `docs/STAGE_D1_QUANT_RESEARCH_BASELINE.md`.
- [x] Test future-bar isolation, next-open fills, data rejection, zero trades, costs, reproducibility and no network access. No production or provisional database was read or changed.
- [x] Verify 531 backend tests passed, 1 skipped; Ruff and `git diff --check` passed. At the time, mypy passed on 122 files when excluding Stage C2 `test_virtual_trading.py`; its 17 typing errors were corrected in the pause checkpoint, and the unrestricted 126-file target now passes.
- [ ] Obtain verified historical identity, corporate-action, point-in-time and independent NSE reference evidence before considering any real-data model training or promotion.

## Stage C2: isolated synthetic virtual trading MVP (implemented locally)

- [x] Add an explicitly seeded INR ₹10,000 virtual SQLite account and virtual-only order audit; keep synthetic quotes out of canonical market-data tables and research inputs.
- [x] Add risk-gated simulated buy/sell preview and submission, fixed virtual paise caps, idempotency, serialized SQLite transaction, FIFO ledger reuse, and authenticated virtual routes.
- [x] Add a separately labeled React virtual panel with dated synthetic prices, cash/holdings/P&L, risk reasons, confirmation and trade history.
- [x] Add temporary-database and browser tests; document the isolated local demonstration in `docs/STAGE_C2_VIRTUAL_ORDER_IMPLEMENTATION.md`.
- [x] Verify backend `524 passed, 1 skipped`; frontend `36 passed`; mypy `84 source files`; frontend lint/build passed. Ruff passed after a final test-format correction.
- [x] Run the local two-server demonstration: browser buy, risk rejection without a ledger write, FIFO sell and reload persistence passed on an isolated virtual database. The canonical and provisional database hashes were unchanged; no broker or real market data was connected.

All 95 FYERS observations remain PROVISIONAL and excluded. No canonical/pilot/provisional or production database migration or write was performed in Stage C2.

## Stage B2.2: offline NSE reference validator

- [x] Review NSE All Reports, current website Terms of Use and data-use policy. The All Reports page lists **CM-UDiFF Common Bhavcopy Final (zip)**; the Terms prohibit systematic or automated website collection. No authorized automated ZIP method was established or implemented.
- [x] List the 14 missing sessions and manual acquisition instructions in `docs/PHASE_B2_2_NSE_OFFLINE_VALIDATION.md`; preserve the five existing ZIPs and all 95 provisional FYERS candles.
- [x] Add `validate-nse-references`: read-only SQLite, existing strict ZIP parser, manifest SHA-256 and provenance checks, expected calendar/ISIN checks, exact signed FYERS-minus-NSE reconciliation, and source database hash comparison. No canonical promotion or research eligibility.
- [x] Verify the current private pilot read-only: 5 valid ZIPs, 25 matches, 70 no-reference comparisons, 14 missing dates, unchanged canonical and staging hashes; incomplete result exits 1.
- [x] Run backend tests (504 passed, 1 skipped), Ruff (pass) and mypy on `app` plus the new tests (80 source files, pass). Synthetic tests include malformed ZIPs, hash/date/ISIN/duplicate failures, signed conflicts, no network and database immutability.
- [ ] Obtain the 14 NSE ZIPs manually through an authorized route, record provenance outside Git, and rerun the offline validator. Do not infer data rights or point-in-time availability from a matching price.

Historical FYERS ticker identity, corporate-action equivalence, NSE usage rights and first-availability time still block verified promotion and real-data ML.

## Stage B1.2: provisional FYERS daily staging (offline)

- [x] Keep the verified FYERS importer and NSE ZIP importer unchanged; add a separate `PROVISIONAL` staging database and private raw-response checkpoints outside Git.
- [x] Validate all five current FYERS master matches, the isolated migrated canonical database, the reviewed NSE calendar and the dated NSE evidence record before any credential access or GET.
- [x] Add exact OHLCV reconciliation against hash-checked local NSE UDiFF ZIPs and existing canonical NSE rows, conflict and missing-session reporting, idempotent checkpoints and fail-closed provisional tests. Provisional rows never enter canonical `daily_prices` or `source_observations`.
- [x] Run offline preflight: five current identities, 19 sessions, 25 local NSE reference rows, 14 dates per symbol without independent NSE identity evidence; no FYERS request. The empty private `provisional.db` is separate from `pilot.db`.
- [x] Verify backend regression (493 passed, 1 skipped), Ruff, mypy on `app` plus the new tests (79 files), and `git diff --check`. The broader optional `mypy app tests` target still reports two pre-existing Upstox test re-export errors; no unrelated test file was changed.
- [ ] Obtain separate approval before any live provisional five-stock pilot. Maximum one GET per symbol, five total; no ML training or canonical promotion.
- [ ] Obtain dated FYERS ticker–ISIN evidence and the remaining NSE identity coverage before a separate verified promotion design.

See `docs/PHASE_7C_B1_2_PROVISIONAL_IDENTITY.md`. Historical FYERS identity remains **unverified**; the original verified preflight remains blocked.

## Stage B1: FYERS historical daily importer (offline implementation)

- [x] Reuse the FYERS historical adapter and provider-neutral mapping, ingestion-run, observation and canonical daily-price tables; keep the NSE ZIP importer intact.
- [x] Add a private raw-response checkpoint, bounded one-window import, verified identity and calendar preflight, integer-paise validation, idempotent resume and explicit agreement/conflict/missing-session reports.
- [x] Add a gated CLI restricted to a private temporary database and raw directory, five pilot symbols, one request per invocation and two live consent flags.
- [x] Add a read-only five-stock pilot preflight before credential loading or any GET: verify temporary-path isolation, current migration, required tables, SQLite integrity, reviewed calendar, master identities and all five existing verified FYERS mappings. Tests confirm missing prerequisites stop without network access.
- [x] Add synthetic offline tests for valid import, repeat import, rollback and resume, identity mismatch, conflict preservation, bad OHLCV, missing/unclassified sessions, secret-safe error handling and no-network dry run.
- [x] Create the approved private `/Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/` root, `raw/` and isolated `pilot.db`; apply Alembic through `0004_market_data_foundation`; confirm SQLite integrity, foreign keys and required tables.
- [x] Import the original local NSE 30 October ZIP into `pilot.db` using the existing importer, yielding 2,703 EQ instruments and prices, including all five pilot symbols with source ISINs; no production database write.
- [x] Retrieve the official public FYERS NSE CM master, record URL, retrieval time and SHA-256 outside Git, and verify that all five current NSE EQ ticker–ISIN rows match the isolated NSE instruments with no ambiguous current match.
- [x] Prepare the NSE Capital Market calendar for 12 October–6 November 2026 from official circular NSE/CMTR/71775 and the NSE equities timings page: 19 regular sessions, 20 October holiday, six weekends. Validate the private calendar JSON offline.
- [ ] Obtain independent evidence that each FYERS ticker mapped to the same ISIN throughout the requested historical interval. The current FYERS master snapshot (all five rows updated 8 November) does not establish this. No FYERS `verified` mapping or identity manifest was created; the actual offline preflight returned `pilot identity manifest is unavailable` and **BLOCKED**. No authenticated history request is authorized in this setup step.

Stage A connectivity returned HTTP 200 and five valid RELIANCE candles for 26–30 October 2026. Stage B1 setup made **no** authenticated FYERS request. It imported only the already-local NSE ZIP into the isolated pilot database. Historical FYERS identity remains blocked, and this setup does not authorize the five-stock pilot. No real-data ML training is eligible. See `docs/PHASE_7C_B1_FYERS_IMPORTER.md`.

## Phase 7D.1: Point-in-time evidence foundation (implemented, committed)

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

Development is paused. Follow the future investigation and restart steps at
the top of this document only when the owner chooses to resume. The Phase 6
usability browser check, reversal-event design and Phase 5 broker-cost golden
example remain pending; none is a reason to bypass the FYERS data gates.

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

## Phase 7C-Data: FYERS historical provider (offline preparation)

- [x] Add a bounded, injectable FYERS v3 history adapter for NSE EQ daily and 1/5/15-minute candles, with runtime-only private token-file access, environment App ID, sanitized errors, one GET and no retries.
- [x] Reuse the provider-neutral paise/OHLC validation contract and Phase 7D.1 intraday evidence contract; retain unknown first-availability and corporate-action status as unverified.
- [x] Add explicit FYERS symbol-master/ISIN comparison, synthetic response and safety tests, and an opt-in single-request, no-persistence connectivity CLI.
- [x] Make one separately approved live Stage A connectivity request: FYERS returned HTTP 200 and five valid RELIANCE daily candles for 26–30 October 2026. It did not persist prices.
- [x] Receive written FYERS confirmation for personal historical OHLC storage, offline backtesting, and statistical/ML development for the user's own activities. Redistribution or commercial rights are not established.

See `docs/PHASE_7C_DATA_FYERS.md` for configuration, the first connectivity command, official API references and access limitations. The five-stock persistent pilot has not run. Existing Upstox HTTP 403 access remains unresolved.
