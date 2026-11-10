# Stage B2.1 — NSE reference coverage and acquisition plan

Status: read-only local audit, 9 November 2026. No HTTP request, download,
database write, canonical promotion or model run was performed. This document
records the observed five-file profile; it does not certify an official NSE
format specification, download entitlement or historical publication time.

## 1. Existing implementation and evidence

- `backend/app/services/price_import_service.py`: `HEADERS` (line 26),
  `FILE_PATTERN` (line 62), `import_price_file` (line 110), `_file_date`
  (line 286), `_parse_archive` (line 298), `_check_file_identity` (line 389)
  and `_parse_eq_row` (line 403). The importer reads one local ZIP in memory,
  requires its one member and exact ordered CSV headers, validates file/row
  dates, stores `EQ` only, parses prices to integer paise and rejects duplicate
  symbol/date rows. Its default is one all-or-nothing transaction. A matching
  file SHA-256 is a no-op; conflicting existing prices or ISINs abort without
  overwrite. `--skip-bad-rows` exists but must **not** be used for reference
  acceptance.
- `backend/app/db/models/prices.py`: `Instrument` (line 23) is keyed by
  symbol and `EQ` series; `InstrumentMapping` (line 45) carries explicit
  identity status and optional validity dates; `IngestionRun` (line 86),
  `SourceObservation` (line 122), `ImportBatch` (line 194) and `DailyPrice`
  (line 231) retain source/provenance, file hash and canonical price links.
  The ZIP importer creates an `nse_zip` mapping with
  `identity_status=legacy_unverified`; it does not claim a dated FYERS mapping.
- `backend/app/services/fyers_provisional_cli.py`: `_evidence` (line 88)
  checks the private evidence manifest and source hashes; `_identities`
  (line 133) compares all five current FYERS master rows with isolated NSE
  instrument ISINs without claiming historical FYERS identity. `run`
  (line 174) loads the calendar and ZIP references before any credential read.
- `backend/app/services/fyers_provisional_service.py`:
  `load_local_nse_references` (line 89) recomputes each ZIP SHA-256, invokes
  the strict parser, requires all five target symbol/ISIN rows and rejects
  duplicate reference keys; `_reconcile` (line 241) compares signed
  FYERS-minus-NSE open/high/low/close paise and volume exactly;
  `verify_nse_references` (line 278) rejects a mismatch between a local ZIP
  and an already stored NSE canonical bar. No provisional row is selected as
  canonical. `stage_fyers_daily_window` (line 298) writes only to the private
  provisional staging database.
- `backend/app/modules/research/readiness_io.py`:
  `load_calendar_evidence` (line 11) parses the reviewed sessions, holidays
  and halts. `backend/app/services/fyers_import_service.py`:
  `_expected_days` (line 75) refuses an unclassified weekday rather than
  treating every weekday as a trading session.
- `backend/app/cli.py` (line 210) exposes `import-prices` for a **local** ZIP
  with required `--source-label` and optional `--database`; it is a database
  write command and was not run for this audit. `stage-fyers-daily` is the
  separate provisional pilot command. The existing import tests are in
  `backend/tests/test_price_import.py`; reference/conflict/checkpoint tests
  are in `backend/tests/test_fyers_provisional_service.py`; calendar and
  preflight tests are also in `backend/tests/test_fyers_import_service.py`.
- Local files: `/Users/mayanksingh/Investment/sample-data/` has exactly the
  five ZIPs shown below, outside Git. The approved private pilot root is
  `/Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/`.
  `historical-identity-evidence.json` records their file names, dates and
  SHA-256 hashes; `nse-calendar.json` records the calendar source;
  `provisional.db` holds the five FYERS runs and their reconciliation states.
  The local `source-provenance.json` records FYERS master and NSE calendar
  sources, **not** the origin URL or acquisition time of these NSE ZIPs.
  No source specification file or recorded download URL was found locally.

## 2. Date-by-date coverage

The reviewed NSE CM calendar contains 19 sessions, 12 October–6 November
2026. It identifies 20 October as a Dussehra holiday; weekends and that
holiday are not missing-file dates. Local ZIP bytes were rehashed and parsed
read-only; each present ZIP had the five expected `EQ` symbol/ISIN rows.
The provisional database was opened read-only to check each stock/date.
Columns R, T, I, H and C mean RELIANCE, TCS, INFY, HDFCBANK and ICICIBANK.
`M` means exact OHLCV match; `—` means `NO_NSE_REFERENCE`.

| Session | Reference | Local filename | SHA-256 | R | T | I | H | C |
| --- | --- | --- | --- | :---: | :---: | :---: | :---: | :---: |
| 2026-10-12 | Missing | — | — | — | — | — | — | — |
| 2026-10-13 | Missing | — | — | — | — | — | — | — |
| 2026-10-14 | Missing | — | — | — | — | — | — | — |
| 2026-10-15 | Missing | — | — | — | — | — | — | — |
| 2026-10-16 | Missing | — | — | — | — | — | — | — |
| 2026-10-19 | Missing | — | — | — | — | — | — | — |
| 2026-10-21 | Missing | — | — | — | — | — | — | — |
| 2026-10-22 | Missing | — | — | — | — | — | — | — |
| 2026-10-23 | Missing | — | — | — | — | — | — | — |
| 2026-10-26 | Present | `BhavCopy_NSE_CM_0_0_0_20261026_F_0000.csv.zip` | `723bee076946c104b9cd56d09ca9b3776b062f19c136acc289c7c279290377e3` | M | M | M | M | M |
| 2026-10-27 | Present | `BhavCopy_NSE_CM_0_0_0_20261027_F_0000.csv.zip` | `d6de1583722c44599c1fa394072a9b496a9041b40e81cd670720f91f2834330e` | M | M | M | M | M |
| 2026-10-28 | Present | `BhavCopy_NSE_CM_0_0_0_20261028_F_0000.csv.zip` | `4e93e09854c535af09dcf656677f7e960dddbe55c38d379b02c98ff2e88edccf` | M | M | M | M | M |
| 2026-10-29 | Present | `BhavCopy_NSE_CM_0_0_0_20261029_F_0000.csv.zip` | `715d25908016922815de203654ea5ef0c22cb32175b315814789f7f42aba5516` | M | M | M | M | M |
| 2026-10-30 | Present | `BhavCopy_NSE_CM_0_0_0_20261030_F_0000.csv.zip` | `ce2b0e6615bbc48990bb32ab081f85568451bb6f16fc54bff7d786faa7d2ca44` | M | M | M | M | M |
| 2026-11-02 | Missing | — | — | — | — | — | — | — |
| 2026-11-03 | Missing | — | — | — | — | — | — | — |
| 2026-11-04 | Missing | — | — | — | — | — | — | — |
| 2026-11-05 | Missing | — | — | — | — | — | — | — |
| 2026-11-06 | Missing | — | — | — | — | — | — | — |

**Confirmed gap: exactly 14 session files and 70 stock-date references.**
All 95 FYERS candles are present only in provisional staging; 25 have exact
local NSE OHLCV matches and 70 have no local NSE reference. No present ZIP
was unregistered, and no registered ZIP was absent or hash-mismatched.

## 3. Official acquisition: known facts and unresolved assumptions

The five samples and `docs/adr/0004-price-data.md` identify an **observed**
cash-market ZIP/CSV profile named
`BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip` (one same-named CSV member,
34 exact ordered columns). The code and local records call it an NSE CM UDiFF
bhavcopy. **Its official report title, version, specification, `ClsPric`
definition and continued availability for all 14 dates are unverified.**
The filename pattern predicts names for missing dates, not that those files
exist or can be acquired.

`README.md` (daily routine), `docs/PHASE_4_BRIEF.md` (data-source rules) and
ADR 0004 describe **operator download from an authorized source**, followed
by local import. The repository contains no NSE acquisition endpoint or
documented public URL template. Do not derive one from filenames, guess a
historical query URL, replay browser requests, bypass cookies/access controls
or use a third-party downloader. The locally recorded
`docs/INDIAN_MARKET_DATA_PROVIDER_REVIEW.md` links to the
[NSE data-use policy](https://www.nseindia.com/static/market-data/nse-data-policy)
and [EOD/historical subscription page](https://www.nseindia.com/static/market-data/eod-historical-data-subscription),
but this audit did not access those pages. Current historical availability,
manual-download rights, private retention/cross-provider comparison rights,
authentication, request limits, charges and any automated retrieval method
must be confirmed against current NSE terms or by NSE in writing. A paid
subscription product is not proof that this public-looking ZIP profile is
covered by a particular licence. No locally documented mechanism establishes
permission for automated retrieval.

## 4. Controlled acquisition design (proposal, not implemented)

Keep three explicit stages and the existing canonical/provisional boundary:

1. **Acquisition, gated.** Start with an operator-reviewed manifest of the
   14 dates above and an approved source/rights record. Prefer manually
   downloaded files while automated retrieval rights and endpoint behavior
   remain unknown. If NSE confirms a permitted endpoint and access method,
   a later narrowly scoped fetcher should accept only an explicit list of
   reviewed session dates, make sequential bounded requests with a documented
   rate limit and no access-control circumvention, and stop on denial or
   unexpected response. Never auto-expand the date range or retry without a
   separately reviewed policy. Save each original ZIP byte-for-byte outside
   Git using private, create-only/atomic file operations; never overwrite.
   Append source URL or approved acquisition route, UTC acquisition time,
   exact filename, date, file SHA-256, byte size, licence/terms reference and
   acquisition status to a private provenance manifest. A later correction
   is a new version, not an in-place replacement.
2. **Validation, offline.** Require a complete reviewed NSE calendar and
   an exact one-to-one mapping of 14 requested dates to 14 ZIPs. Reuse
   `_file_date`/`_parse_archive` without extraction. Require the observed
   member name, UTF-8/BOM handling, exact headers and order, matching
   `TradDt`/`BizDt`, `Sgmt=CM`, `Src=NSE`, `FinInstrmTp=STK`, `EQ` rows,
   positive Decimal prices with at most two places, valid OHLC ranges,
   nonnegative integer volume and no duplicate symbol/date. For each date,
   require exactly one row for each of the five reviewed symbols and an ISIN
   matching the expected NSE identity. Reject the whole reference file on
   any malformed or conflicting target row; do not use `--skip-bad-rows`.
   Rehash on every use and compare with immutable provenance. Treat a format
   change as a new reviewed profile, never a permissive parser fallback.
3. **Reconciliation, offline/read-only.** Compare all 19 session dates and
   five symbols against the already staged FYERS records, preserving both
   sources. Report signed FYERS-minus-NSE open/high/low/close differences in
   paise and signed integer-volume differences per stock/date, plus missing,
   duplicate or conflicting references and any ISIN mismatch. Require 95
   classified stock-date pairs; an unresolved difference or missing pair
   fails the reference-coverage gate. No tolerance, correction, canonical
   selection or promotion is inferred. A repeat run with the same hashes
   must be idempotent and produce the same report.

Acceptance for *reference coverage* requires 14 newly validated files,
19/19 dated references per stock, 95/95 classified comparisons, zero invalid
target rows, duplicate keys or unreviewed revisions, and an audit record for
every source. A price/volume conflict requires investigation and explicit
resolution; it cannot silently pass. Full matching OHLCV would close only
the NSE reference coverage gap, **not** research eligibility.

## 5. Independent blockers and next step

- **Historical FYERS identity:** an NSE file can show symbol/series/ISIN on
  its own trading date. It cannot prove that FYERS' historical
  `NSE:SYMBOL-EQ` key returned that same ISIN throughout the interval.
  Require a dated FYERS master history or written provider confirmation for
  the exact ticker, ISIN and date interval before verified mapping.
- **Corporate actions and price basis:** five exact days do not establish
  whether FYERS revises older candles for splits, bonuses, dividends or other
  actions. Require dated action records, both providers' adjustment policies
  and a reviewed unadjusted/adjusted comparison across relevant ex-dates.
  Do not compute returns across unresolved actions.
- **Point-in-time availability:** an NSE ZIP acquired now proves its contents
  at acquisition, not its original release time. FYERS' observed daily epoch
  is 00:00 UTC, which is not evidence of completion or first availability.
  Require provider publication/availability evidence and a conservative
  decision-time rule before historical features or model evaluation.
- **Licensing and broader research:** confirm rights for downloading,
  storage, backups, cross-provider comparison and intended personal research;
  separately verify any ML-training or commercial rights. This audit grants
  none. A five-stock, 19-session pilot also lacks sufficient history and
  historical-universe coverage for meaningful real-model benchmarking.

**Recommended Stage B2.2:** obtain explicit approval for an *offline-only*
reference validator and reconciliation report that accepts an operator-supplied
14-date manifest and local ZIPs in a private directory. Add synthetic tests
for missing/wrong-date/member/header/ISIN/duplicate/hash/conflict cases and
read-only preservation of both databases. Do not add a network fetcher or
acquire any file until the official acquisition route, terms and budget are
reviewed and separately approved. Keep verified promotion and ML training
out of B2.2.
