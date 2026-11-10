# Stage B1: Checkpointed FYERS daily importer

## Status and scope

The importer is implemented and tested offline with invented candles. Stage A's
single RELIANCE daily connectivity request returned HTTP 200 and five valid
candles for 26–30 October 2026. **No FYERS history has been imported into a
database by this stage.** The approved private pilot database is now migrated
and seeded from the local NSE ZIP, and the reviewed NSE calendar is present.
The current FYERS master matches all five NSE identities, but does not prove
their FYERS ticker history over the requested window. No FYERS mappings or
historical identity manifest were created, so offline preflight is **BLOCKED**.
The user has not authorized the five live pilot requests in this setup step.
This does not establish readiness for ML
training or backtesting.

The existing FYERS v3 client supplies daily candles; the importer uses the
existing `instruments`, `instrument_mappings`, `ingestion_runs`,
`source_observations`, and `daily_prices` tables. No migration or parallel
daily table is needed. The NSE ZIP importer remains available. Ledger, risk,
strategies, and ML admission rules are unchanged.

## Identity, provenance and price selection

Each request requires a reviewed FYERS symbol-master record, matching the
existing internal EQ symbol and ISIN, plus an operator-reviewed date interval
and evidence note. A current symbol master does not by itself prove historical
ticker validity; a changed ticker or ISIN must be resolved before retrieval.
The pilot CLI accepts only RELIANCE, TCS, INFY, HDFCBANK, and ICICIBANK.

The original bounded HTTP response is saved in a private mode-600 JSON
envelope under a mode-700 raw directory. The envelope records the exact source
URL, retrieval time (UTC), response SHA-256, reviewed identity and original
bytes encoded as Base64. It must remain outside Git. Parsed observations
retain each original candle's fields, source record key, source timestamp,
retrieval timestamp, payload hash, mapping and ingestion run. The provider
does not claim a point-in-time availability timestamp or an adjusted price.
Prices are validated and stored as integer paise.

For a date without a canonical daily price, the FYERS observation is selected
and the canonical row is inserted. Where an NSE or other canonical price
already exists, it is never replaced: an equal FYERS bar is marked agreement;
a different bar is retained as a conflict for review. A committed run and its
matching private raw file form the checkpoint. Repeating the same window does
not call FYERS or add rows. If the database transaction fails after the raw
response is saved, the next attempt parses the raw file without another GET.
If a committed checkpoint has lost its raw file, or its hash or identity does
not match, the importer stops. Use one raw directory per isolated pilot DB.

## Calendar and missing data

An operator-reviewed JSON calendar classifies every weekday in the bounded
window as an exchange session or holiday; special sessions are explicit.
Verified full-session halts are excluded from expected sessions. Missing
expected sessions are reported; they are never forward-filled. A returned
candle outside the reviewed sessions stops the import. Each invocation is
limited to one symbol, one FYERS GET and at most 31 calendar days. The end
date must precede today's Asia/Kolkata date, so every daily bar is complete.
FYERS 401, 403, 429 and malformed responses stop without a retry.

## Bounded five-stock pilot preparation (offline only)

Prepare a private, migrated **temporary** SQLite database containing the
five NSE EQ `instruments` rows with independently verified ISINs and five
time-bounded, `verified` FYERS `instrument_mappings`. It must be separate from
`data/`. The existing NSE ZIP importer can populate instrument identities in
that temporary database, but **there is no approved standalone FYERS master
import command**. Do not invent IDs or mappings. Have the five mappings
reviewed and added through the existing `add_verified_mapping` service before
the pilot; stop if that setup is unavailable. Prepare a private FYERS master subset JSON keyed by the
exact ticker, e.g. `NSE:RELIANCE-EQ`, with `exSymbol`, `symTicker`, `exSeries`,
`exchangeName`, and `isin` from the official symbol master. Prepare a reviewed
calendar JSON with `source_note`, `sessions` (date, opens_local, closes_local,
special), `holidays`, and `halts` (UTC start/end). Keep these inputs and raw
outputs outside the repository. Confirm that the historical identity interval
is supported by independent evidence, rather than assuming the current master
applied throughout the past. The private identity manifest must have exactly
the five pilot symbol keys, each with `ticker`, `isin`, `valid_from`,
`valid_to` and `evidence_note`. The CLI checks every manifest identity against
the master and the database before any request.

The proposed window is **2026-10-12 through 2026-11-06**. The reviewed NSE
calendar contains **19 regular sessions**, one weekday holiday (20 October,
Dussehra) and six weekend days. It cites NSE Capital Market circular
[NSE/CMTR/71775](https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf)
dated 12 December 2025 and the [NSE equities holiday and market-timing page](https://www.nseindia.com/resources/exchange-communication-holidays?apiversion=v2),
reviewed on 9 November 2026. No special session was identified inside this window.

The private root is `/Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/`:
`pilot.db` and `raw/` are the only approved live-write locations. Migration
`0004_market_data_foundation` was applied there. The existing NSE ZIP importer
loaded the original 30 October 2026 ZIP into that isolated database: 2,703 EQ
rows stored, 938 other-series rows skipped, zero rejected. This also created
the five required NSE `instruments` rows without inventing IDs. SQLite
integrity, foreign-key and application ledger-integrity checks passed.

The official current [FYERS NSE CM master](https://fyers.in/api-docs/v3/api/market-information/symbol-master/symbol-master-json/)
was retrieved from `https://public.fyers.in/sym_details/NSE_CM_sym_master.json`
on 9 November 2026. Its SHA-256, source URL and retrieval time are recorded
outside Git in `source-provenance.json`. Its five current `NSE:...-EQ` rows
each match the corresponding NSE ZIP symbol and ISIN, with no ambiguous
NSE EQ match. All five master rows have `lastUpdate=2026-11-08`; this is **not**
evidence that the FYERS ticker mapped to that ISIN throughout 12 October–6
November. Do not add a `verified` historical mapping from this snapshot alone.

After independent historical identity evidence and the five mappings are
available, run the offline check below with the real reviewed interval and
evidence note. It makes no HTTP request or credential read:

```bash
cd /Users/mayanksingh/.codex/worktrees/phase-7b-integration/investment-agent
PILOT_DIR=/Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1
PYTHONPATH=backend /Users/mayanksingh/Investment/investment-agent/.venv/bin/python \
  -m app.cli import-fyers-daily --help
PYTHONPATH=backend /Users/mayanksingh/Investment/investment-agent/.venv/bin/python \
  -m app.cli import-fyers-daily \
  --database "$PILOT_DIR/pilot.db" --raw-dir "$PILOT_DIR/raw" \
  --master-json "$PILOT_DIR/fyers-master-subset.json" \
  --pilot-identities-json "$PILOT_DIR/pilot-identities.json" \
  --calendar-json "$PILOT_DIR/nse-calendar.json" \
  --symbol RELIANCE --ticker NSE:RELIANCE-EQ --isin INE002A01018 \
  --valid-from '<reviewed start date>' --valid-to '<reviewed end date>' \
  --identity-evidence '<reviewed source and date>' \
  --from-date 2026-10-12 --to-date 2026-11-06 --max-requests 1 \
  --preflight-only
```

The actual offline preflight currently stops with `pilot identity manifest is
unavailable`. Once independent historical identity evidence is established,
run this check once; it checks all five symbols. A **separate authorization**
is required before replacing `--preflight-only` with
`--allow-live --confirm-persist` and running one command per symbol. That caps
the future pilot at five GETs with zero automatic retries. The
CLI reads `FYERS_APP_ID` from the private `~/fyers-auth/.env` and the token
from `~/fyers-auth/.access_token` only after all live gates pass. Neither is
printed or stored. The live command requires the exact approved database and
raw directory; offline tests may use OS temporary paths. It checks private
ownership and permissions. A response
report includes candle, observation, duplicate, canonical, agreement,
conflict and missing-session counts, plus the response hash. No market data
is printed.

## Remaining limits

- The current master subset and reviewed calendar are present, but five FYERS
  ticker-to-ISIN effective-date intervals are unverified. No five-stock
  historical identity manifest or `verified` FYERS mapping was created.
  Until those exist, the live pilot is blocked. The offline preflight refuses
  a missing database, migration, required table, mapping or calendar.
- FYERS daily candles provide neither verified corporate-action adjustment nor
  point-in-time availability. Readiness gates must continue to fail closed.
- A successful API response does not prove completeness, licensing beyond the
  user's written personal-use permission, or model quality.
- Only NSE EQ daily candles are persisted by this importer. Intraday remains
  separate research work. There is no automatic pagination or rate-limit retry.
- The CLI accepts a single 31-day window. Wider backfills require separately
  planned, approved windows and historical identity/calendar reviews.
