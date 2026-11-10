# Stage B1.2: provisional FYERS daily staging

## Boundary

Historical FYERS ticker-to-ISIN validity is still unverified for 12 October–6
November 2026. `PROVISIONAL` means a current FYERS master row matches the
internal NSE EQ symbol and ISIN, but does **not** establish historical identity.
`VERIFIED` remains the existing, time-bounded `instrument_mappings` state used
by the original `import-fyers-daily` command. This stage creates no verified
mapping and does not relax `resolve_instrument`, readiness or evidence gates.

Provisional data goes only into a separate private `provisional.db` under the
approved pilot root. It has `provisional_runs` and `provisional_candles` tables;
both identify records as provisional. The canonical `pilot.db` is opened
`mode=ro` for identity and NSE price comparison. No provisional row enters
`source_observations` or `daily_prices`, which are the only database source for
the research, backtest, portfolio and price-read paths. There is no automated
promotion and no database migration to the production schema.

The existing FYERS provider parses daily OHLCV, timestamp order, completed
candles and integer paise. The staging command checks all five current master
identities, the migrated canonical schema, the reviewed NSE calendar, private
storage paths and the Stage B1.1 evidence record before credentials are read.
The evidence record lists only the five NSE UDiFF dates, 26–30 October; the
remaining **14 of 19 sessions** are explicitly reported as lacking independent
NSE identity evidence. A current FYERS master snapshot cannot fill that gap.

Each bounded response is saved first in a private, immutable JSON envelope
containing original bytes, source URL, UTC retrieval time, response hash,
current-master hash and `PROVISIONAL` identity state. A single staging SQLite
transaction records the parsed candles. A matching checkpoint and raw file
make repeat submissions idempotent; after a transaction failure, the saved raw
response can be replayed without another GET. A missing, altered or mismatched
raw checkpoint stops. Source record keys and symbol/date checks reject
conflicting redelivery; nothing overwrites an earlier staged or canonical bar.
There is one GET at most per invocation, with no retry.

Reconciliation compares each FYERS bar with a hash-checked, already-local NSE
UDiFF ZIP row or an existing NSE ZIP canonical bar for the same internal
instrument and date. Both references must agree if present. The ZIPs are read
in memory using the existing strict NSE parser; they are never copied, extracted
or imported into the canonical database by this command. It reports `MATCH`, `CONFLICT` with
signed open/high/low/close/volume differences, or `NO_NSE_REFERENCE`.
Comparison is exact, with no invented tolerance. Missing sessions are reported
separately and never filled. NSE identity evidence coverage is reported
separately from the presence of a canonical NSE price: an exact price match
does not prove FYERS historical ticker identity.

## Offline preflight and proposed pilot

The private paths are:

- canonical reference: `~/InvestmentAgentData/pilots/fyers-b1/pilot.db`
- provisional staging: `~/InvestmentAgentData/pilots/fyers-b1/provisional.db`
- immutable raw envelopes: `~/InvestmentAgentData/pilots/fyers-b1/raw/`
- evidence: `~/InvestmentAgentData/pilots/fyers-b1/historical-identity-evidence.json`
- current FYERS master and NSE calendar: same private pilot root

The offline preflight succeeded on 9 November 2026: five current identities,
19 calendar sessions, 25 hash-checked local NSE reference rows, 14 sessions
per symbol without NSE identity evidence;
no credential read or FYERS request. It created an empty private staging DB.
The original **verified** import preflight remains blocked.

The following command is a **proposal for a later, separately approved**
five-request pilot. Run it once for each of `RELIANCE`, `TCS`, `INFY`,
`HDFCBANK`, `ICICIBANK`; no loop or request should be started before the
user approves live retrieval and storage. For offline validation omit both
live flags and use `--preflight-only`.

```bash
cd /Users/mayanksingh/.codex/worktrees/phase-7b-integration/investment-agent
PILOT_DIR="$HOME/InvestmentAgentData/pilots/fyers-b1"
PYTHONPATH=backend /Users/mayanksingh/Investment/investment-agent/.venv/bin/python \
  -m app.cli stage-fyers-daily \
  --database "$PILOT_DIR/pilot.db" \
  --staging-database "$PILOT_DIR/provisional.db" \
  --raw-dir "$PILOT_DIR/raw" \
  --master-json "$PILOT_DIR/NSE_CM_sym_master.json" \
  --evidence-json "$PILOT_DIR/historical-identity-evidence.json" \
  --calendar-json "$PILOT_DIR/nse-calendar.json" \
  --nse-zip-dir /Users/mayanksingh/Investment/sample-data \
  --symbol RELIANCE --from-date 2026-10-12 --to-date 2026-11-06 \
  --max-requests 1 --allow-live --confirm-persist
```

The CLI prints no credentials or raw candle payloads. `--preflight-only` reads
no FYERS App ID or token and makes no request. The live form is restricted to
the exact approved pilot paths and dates. The five executions would have a
maximum of five authenticated GETs. The provider makes no fallback request.
Only the approved personal-use rights apply; no redistribution or commercial
data rights are inferred.

## Evidence needed before verification

Promotion is **not implemented**. A future reviewed promotion would require:

1. Dated NSE symbol, EQ series and ISIN evidence covering each observed date,
   with corporate actions and ticker/ISIN changes assessed for actual identity
   effects. The 26–30 October local ZIP rows cover only five dates.
2. Dated FYERS symbol-master history or written FYERS confirmation that each
   `NSE:SYMBOL-EQ` ticker identified that ISIN throughout the requested
   interval. The current 8 November snapshot is insufficient.
3. Resolution of every price/volume conflict and missing expected session;
   preserve both source records and the review decision. A price match alone
   cannot satisfy the provider-identity requirement.
4. Separate review of corporate-action adjustment, point-in-time availability,
   data licensing, universe history and research-readiness criteria before any
   real-model training or backtest.

Only after a separate approval and explicit, audited promotion procedure may
provisional observations be considered for canonical selection. The existing
verified importer and NSE ZIP importer remain unchanged.

## Daily candle timestamp and point-in-time boundary (9 November 2026)

FYERS [Historical Data API](https://fyers.in/api-docs/v3/api/market-data/historical-data)
returns an epoch as the first element of each OHLCV candle. FYERS
[support](https://support.fyers.in/portal/en/kb/articles/does-the-timestamp-in-the-history-api-for-each-candle-indicate-its-start-time)
describes candle timestamps generally as the start of an interval, while its
[completion guidance](https://support.fyers.in/portal/en/kb/articles/is-there-any-delay-in-the-data-provided-by-the-history-api)
says accurate completed values are available only after that interval finishes.
Neither source establishes what **00:00 UTC (05:30 IST)** means for an NSE
*daily* candle. The RELIANCE pilot returned that time for every daily row;
it precedes the regular exchange session. Its precise daily convention is
**UNVERIFIED**: do not treat it as the exchange open, candle completion or
data availability timestamp.

- **Trading date:** the exchange-local date assigned to the daily OHLCV row.
- **Exchange session:** the reviewed NSE trading day, including its actual
  session boundaries; it is not inferred from FYERS' daily epoch.
- **Candle timestamp:** the provider's raw epoch, preserved as UTC in
  provisional `event_at_utc`; that column name does not prove an event time.
- **Candle completion:** the full session has ended and its daily OHLCV can
  be final. The parser's past-date check is a minimum offline guard, not a
  verified provider publication time.
- **Data availability:** when a user could first have observed the completed
  bar from FYERS. This is **unknown**; retrieval time proves only that it
  existed by the later retrieval, not when it first became available.

Provisional rows have no verified `available_at_utc`, live in a separate
database, and cannot enter canonical `source_observations` or `daily_prices`.
Research, backtesting and portfolio reads use those canonical tables only.
Promotion would require separate historical identity evidence and an audited
availability rule or provider confirmation. No timestamp reinterpretation or
automatic promotion is authorized by this pilot.

## B1.4 TCS pilot result (9 November 2026)

One authorized, no-retry FYERS Historical Data GET for `NSE:TCS-EQ`
(`INE467B01029`) returned HTTP 200 / FYERS `s=ok`, `code=200`.
All 19 reviewed trading sessions from 12 October through 6 November were
staged as `PROVISIONAL`; there were no missing or duplicate sessions and
offline parsing found no invalid OHLCV row. Every returned daily epoch was
00:00 UTC. The five 26–30 October rows matched the existing local NSE UDiFF
OHLCV exactly; the other 14 dates have no independent local NSE reference.
There were no signed price or volume discrepancies. The private raw response
and staged response hashes matched, SQLite integrity and foreign-key checks
passed, and the canonical `pilot.db` SHA-256 was unchanged. No verified FYERS
mapping, canonical price, research observation or promotion was created.
Historical FYERS identity and the exact daily publication time remain open.
An INFY provisional pilot would require separate authorization and the same
single-stock preflight; these TCS results do not authorize any next request.
