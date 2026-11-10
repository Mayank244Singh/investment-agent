# Stage D2.5 — Provisional FYERS daily history collector

Implemented offline on 10 November 2026. **No five-year FYERS request was made.**
At implementation time the five-year preflight was **BLOCKED** because the
only reviewed NSE calendar covered 12 October–6 November 2026, not the
target 2021–2026 range. Stage D2.6 subsequently created the full calendar
and passed offline preflight using the full private FYERS symbol master that
matches the identity manifest. See
`docs/STAGE_D2_6_NSE_TRADING_CALENDAR.md` for the current result.
The existing 95 pilot candles and canonical NSE data remain unchanged.

## Architecture and evidence boundary

- `collect-fyers-history` plans contiguous, inclusive daily windows of at most
  **366 calendar days**, the current [FYERS Historical Data API limit](https://fyers.in/api-docs/v3/api/market-data/historical-data).
  The target 10 November 2021–9 November 2026 spans 1,826 calendar days:
  **five windows per stock, 25 first-attempt requests for five stocks**.
  The observed Stage A range included both endpoint dates; the adapter checks
  response dates against the requested window.
- The command reuses `FyersHistoricalDataProvider` and the existing
  `provisional_runs` / `provisional_candles` schema, private immutable raw
  checkpoints, integer-paise parser, current master/ISIN cross-check,
  collision detection and NSE reconciliation. The canonical `pilot.db` is
  opened read-only. The `provisional.db` and `raw/` paths remain the only
  approved live destinations; a private SQLite snapshot is made before the
  first possible staging write. Existing one-window commands keep their
  31-day default and existing behavior.
- Every calendar weekday in **every** requested window must be classified by
  an operator-reviewed NSE calendar as a session or holiday. Special sessions
  and full-session halts must be explicit. No calendar inference or gap
  forward-fill is performed. Missing expected sessions, empty windows,
  mismatched NSE prices, duplicate conflicts and malformed responses stop
  further collection for review. Exact duplicates remain unchanged.
- The example universe records current FYERS tickers/ISINs and JSON pointers
  into the private identity evidence. These are **not** historical FYERS
  mapping intervals. Each staged run remains `PROVISIONAL`; corporate-action
  basis and first historical availability remain `UNVERIFIED`. Neither
  provisional observations nor their reports become canonical prices,
  research datasets, ML training inputs, backtest bars or portfolio quotes.
- The provider keeps its no-redirect transport, bounded response and timeout.
  The collector allows at most three attempts per window, retrying only
  network failure, HTTP 429/5xx or documented rate-limit codes, with backoff.
  Its 1.3-second spacing is below FYERS' published [Standard Data API rate
  limits](https://support.fyers.in/portal/en/kb/articles/what-are-the-fyers-api-rate-limits-for-standard-and-prime-plans)
  of 5 requests/second and 50/minute. `--max-requests` counts **every** GET,
  including retries. HTTP 401/403, malformed data and identity conflicts do
  not retry. A token problem requires the existing manual refresh helper.
  The token is read from the private file at each new request; App ID comes
  from `FYERS_APP_ID`. Neither is printed, logged or stored.
- Committed window rows plus raw response checksums provide restart state.
  A raw response saved before a database interruption is parsed on resume
  without a second GET. `--resume` is required when a planned checkpoint
  exists. The private JSON report records windows, request count, candle
  dates/counts, missing sessions, NSE comparison states, failures, hashes,
  destination, and `research_eligible: false`.

## Mandatory missing prerequisite

Create a **private, source-reviewed** calendar at
`/Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/nse-calendar-20211110-20261109.json`
with the existing `source_note`, `sessions`, `holidays`, `halts` schema.
It must cover the entire target range and distinguish holidays from missing
data. Do not generate it from weekdays alone. The existing
`nse-calendar.json` has only 19 reviewed sessions. Current offline command:

```bash
cd /Users/mayanksingh/.codex/worktrees/phase-7b-integration/investment-agent
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend .venv312/bin/python -m app.cli \
  collect-fyers-history \
  --universe-file docs/examples/fyers-five-stock-universe.json \
  --from-date 2021-11-10 --to-date 2026-11-09 \
  --database /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/pilot.db \
  --staging-database /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/provisional.db \
  --raw-dir /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/raw \
  --master-json /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/fyers-master-subset.json \
  --evidence-json /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/historical-identity-evidence.json \
  --calendar-json /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/nse-calendar.json \
  --nse-zip-dir /Users/mayanksingh/Investment/sample-data \
  --supplemental-manifest /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/nse-reference-supplement-20261109.json \
  --max-requests 25 --preflight-only
```

Observed: `FYERS collection BLOCKED: calendar does not classify 2021-11-10`
(exit 2). The command made **zero** FYERS requests, read **zero** credentials,
and wrote **zero** database or raw files. Replacing the calendar path with a
complete reviewed file is the next prerequisite; repeat `--preflight-only`
and require `preflight: PASS` before any live run.

## Exact future five-stock commands — **do not run until separately approved**

At the time of this Stage D2.5 plan, the path named `full_calendar` below did
not exist. Stage D2.6 created it from reviewed NSE notices and the exchange's
published trading rule. The offline preflight passes when `--master-json`
points to the full private FYERS master, not its five-row subset. FYERS'
Standard plan documents a
rolling last-five-year daily window, so the earliest 2021 dates may cease to
be available as time advances; do not silently shift the requested period.

```zsh
cd /Users/mayanksingh/.codex/worktrees/phase-7b-integration/investment-agent
pilot=/Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1
full_calendar=$pilot/nse-calendar-20211110-20261109.json
common=(
  --universe-file docs/examples/fyers-five-stock-universe.json
  --from-date 2021-11-10 --to-date 2026-11-09
  --database "$pilot/pilot.db" --staging-database "$pilot/provisional.db"
  --raw-dir "$pilot/raw" --master-json "$pilot/NSE_CM_sym_master.json"
  --evidence-json "$pilot/historical-identity-evidence.json"
  --calendar-json "$full_calendar"
  --nse-zip-dir /Users/mayanksingh/Investment/sample-data
  --supplemental-manifest "$pilot/nse-reference-supplement-20261109.json"
  --token-file /Users/mayanksingh/fyers-auth/.access_token
  --max-requests 25
)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend .venv312/bin/python -m app.cli \
  collect-fyers-history "${common[@]}" --preflight-only
# Only after a separate live-acquisition approval and a passing preflight:
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend .venv312/bin/python -m app.cli \
  collect-fyers-history "${common[@]}" --allow-live --confirm-persist
# After an interruption, with a fresh manually renewed token if needed:
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend .venv312/bin/python -m app.cli \
  collect-fyers-history "${common[@]}" --resume --allow-live --confirm-persist
```

The first command has a 25-GET **attempt** budget; retries consume that
budget, so more than one approved invocation may be needed to complete all
windows. A dry run uses `--dry-run` in place of `--preflight-only` and also
makes no request or write. No automatic login/OTP renewal occurs.

## Storage and expansion

The FYERS adapter caps each response at 2 MB, and existing private raw
envelopes at 3 MB. Thus 25 checkpoints have a conservative **75 MB maximum
raw-envelope bound**, plus the growing provisional SQLite file, snapshots and
small reports; actual usage is unknown until retrieval. Raw files and
databases remain outside Git. One window may contain fewer candles due to
listing history or provider gaps; the collector reports rather than fills.

After the five-stock pilot is separately approved and reviewed, a 300–500
stock expansion would use the **same command** with a new private, reviewed
version-1 universe JSON (up to 500 entries), a matching current FYERS master
subset and identity evidence, the full calendar, `--resume`, and a separately
approved request budget. At five windows per stock that is approximately
1,500–2,500 first attempts and up to 4.5–7.5 GB at the 3 MB raw-envelope
bound. Do not launch that expansion from the included five-stock example.
Historical ticker identity, corporate actions, source availability and
historical-universe membership still need independent evidence before any
real-data research eligibility decision.

## Verification and limits

Synthetic offline tests cover five-year/leap-date windows, strict universe
mapping, read-only preflight, malformed/empty/invalid OHLCV responses,
deduplication, conflicts, crash-after-raw resume, token failure, rate limit,
network failure, request budget, private snapshots, canonical/virtual data
hash preservation and no socket use. No package was added. `.venv312` can run
the CLI but lacks pytest/Ruff/mypy; verification used the existing project
`.venv` with Python 3.12. The full backend test suite and static-check results
are reported in the implementation handoff; unrelated pre-existing mypy
errors in virtual-trading, trend-research and Upstox test files are not changed
by this collector.
