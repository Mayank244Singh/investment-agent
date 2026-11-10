# Stage D2.4 — Historical data research eligibility

Date: 10 November 2026. Scope: five NSE EQ instruments, 19 trading sessions
from 12 October through 6 November 2026, and 95 **provisional** FYERS daily
candles. This is an offline, read-only evidence review. No status was promoted.
`docs/STAGE_D2_1_REAL_DATA_READINESS.md` is absent from this worktree; the
existing research readiness code and Stage D2.3 report were inspected directly.

## Decision

**BLOCKED for every stock.** The 19 hash-verified NSE ZIPs establish the
symbol, EQ series and ISIN present in each exchange file, and all 95 FYERS
OHLCV observations match those NSE rows exactly. They do not establish a
historically valid FYERS ticker-to-ISIN mapping, a verified adjustment/no-action
status, or the first time a completed FYERS candle could be used for a signal.
Nineteen sessions also cannot warm up the existing 30-session slow moving
average or support meaningful model evaluation.

## Gate 1 — Trading-date-specific instrument identity

The existing strict ZIP parser and Stage D2.3 validator checked all 19 dates,
including NSE `CM`, `STK`, `EQ` and `ISIN` fields. A separate read-only parse
found exactly one EQ row per stock/date, with the same ISIN on all 19 dates.
No symbol or ISIN change appears **within these exchange rows**. This is
historical **NSE** identity evidence for observed dates; it does not prove
which ticker FYERS used at the time of original publication or rule out a
change outside the pilot interval. The present FYERS symbol master corroborates
current identity only. The private historical-identity manifest explicitly
records no dated FYERS master or provider confirmation for the full interval.
In `pilot.db`, each of these five `nse_zip` mappings is
`legacy_unverified` with both validity dates NULL; there is no dated,
`verified` FYERS mapping. All five staging runs retain `PROVISIONAL` state.

| Stock | NSE ticker / series on all 19 dates | NSE ISIN on all 19 dates | Current FYERS ticker | Historical FYERS mapping | Verdict |
| --- | --- | --- | --- | --- | --- |
| RELIANCE | RELIANCE / EQ | INE002A01018 | `NSE:RELIANCE-EQ` | No dated verification | BLOCKED |
| TCS | TCS / EQ | INE467B01029 | `NSE:TCS-EQ` | No dated verification | BLOCKED |
| INFY | INFY / EQ | INE009A01021 | `NSE:INFY-EQ` | No dated verification | BLOCKED |
| HDFCBANK | HDFCBANK / EQ | INE040A01034 | `NSE:HDFCBANK-EQ` | No dated verification | BLOCKED |
| ICICIBANK | ICICIBANK / EQ | INE090A01021 | `NSE:ICICIBANK-EQ` | No dated verification | BLOCKED |

No ambiguous or conflicting symbol/ISIN pair was found in the **five-stock,
19-date NSE sample**. Exact price agreement is corroboration, not historical
FYERS identity proof. The existing `FyersInstrument` and verified importer
require an explicit, time-bounded mapping; this review creates none.

## Gate 2 — Corporate-action and price-basis equivalence

The NSE daily price model records an `unadjusted` basis. The 95 exact OHLCV
matches show FYERS delivered the same numeric values as the NSE files for
this bounded sample, including the five dates that were already canonical.
They do **not** document FYERS' adjustment policy, adjustment vintages or
total-return treatment. A read-only comparison of each stock's NSE
`PrvsClsgPric` with the prior observed NSE `ClsPric` found no discontinuity
on the 18 adjacent session pairs per stock. That is only a diagnostic; it is
not an exhaustive split, bonus, dividend or rights audit. An action can leave
these two fields equal, and dividend cash flows are absent from OHLCV returns.

| Stock | Local action evidence for pilot window | Adjustment-equivalence verdict |
| --- | --- | --- |
| RELIANCE | No stock-specific action record in the reviewed local evidence; prior-close diagnostic has no discontinuity | UNVERIFIED |
| TCS | Existing private identity review cites an NSE EQ interim dividend ex-date of 14 October 2026; this does not by itself change ticker or ISIN | UNVERIFIED; dividend-adjusted total return cannot be inferred |
| INFY | No stock-specific action record in the reviewed local evidence; prior-close diagnostic has no discontinuity | UNVERIFIED |
| HDFCBANK | No stock-specific action record in the reviewed local evidence; prior-close diagnostic has no discontinuity | UNVERIFIED |
| ICICIBANK | No stock-specific action record in the reviewed local evidence; prior-close diagnostic has no discontinuity | UNVERIFIED |

“No record found locally” does not mean “no corporate action.” Splits, bonus
issues, rights issues and symbol/ISIN changes need a dated, exhaustive action
review per stock. Price equality does not establish total-return correctness.
No historical prices were adjusted here.

## Gate 3 — Point-in-time availability and backtest boundary

The provisional rows preserve the original FYERS daily epoch: **00:00 UTC
(05:30 IST)** on each of the 19 trading dates. This precedes the regular NSE
session; it is a provider candle timestamp, **not** a verified completion or
first-availability time. The parser marks a daily candle complete when its
trade date predates the retrieval date. That shows it was complete by the
later retrieval, not when it first became usable historically. The private
checkpoint retrieval time and Stage D2.3 ZIP download time are also later
acquisition evidence, not first-availability evidence. Provisional rows have
no verified `available_at_utc` field.

The existing `PriceData.view` / `HistoryView` exposes only bars through the
decision date, and the simulation queues decisions for the next session's
open (`backend/app/modules/backtest/data.py`, `engine.py`). This prevents
ordinary future-*date* access, but the stored-price loader has no
per-observation availability field. Next-open execution alone therefore
cannot certify that a FYERS daily bar was available by that historical open.
A conservative rule would require the completed bar **after** its exchange
session close and only before a later decision, with documented availability
evidence or an explicitly reviewed assumption. No such rule is treated as
verified here; midnight UTC is never used as availability.

The research core rejects `identity_verified=False`, unverified adjustment
status, and missing `available_at_utc`. The provider-neutral readiness
assessment likewise reports these failures; its result cannot enable the
synthetic-only ML training path. Physically, `provisional.db` is separate from
the canonical `source_observations` / `daily_prices` read by research,
backtesting, valuation and portfolio paths (`fyers_provisional_service.py`).

## Remaining evidence and minimum next action

1. Obtain dated FYERS symbol-master archives or written FYERS confirmation
   tying each provider ticker to its NSE EQ ISIN over **12 October–6 November
   2026**, including effective dates and any corrections. Reconcile this with
   the already verified day-specific NSE records; do not infer validity from
   today's master or prices alone.
2. Obtain dated NSE corporate-action disclosures for each stock covering the
   interval, including no-action evidence where applicable, and FYERS'
   historical adjustment policy/version. Account for the cited TCS dividend
   before interpreting returns. Extend the audit when extending the window.
3. Obtain FYERS daily candle completion/first-availability evidence or propose
   an explicit conservative post-session policy for separate review. Test the
   policy against decision times; do not backdate availability to 00:00 UTC.
4. Acquire enough authorized, historically identified sessions and a
   defensible historical universe before meaningful strategy or ML evaluation.
   FYERS' written personal-research permission is recorded in project docs;
   NSE reference-data rights and broader uses must be assessed separately.

These items require evidence and a separate promotion decision. No current
database row or mapping should be marked `VERIFIED` from this review.

## Checks performed

- Reused the Stage D2.3 strict validator result: CLI exit 0, **19/19** ZIPs
  valid, **95/95** exact FYERS–NSE OHLCV matches, no missing dates, and
  `research_eligible: false`. Its files and databases were unchanged.
- Parsed the 19 local ZIPs read-only again for the five EQ symbol/ISIN pairs
  and compared previous close to the prior session's close; no identity
  conflict or prior-close discontinuity was found in this bounded sample.
- Queried `pilot.db` and `provisional.db` via SQLite `mode=ro&immutable=1`:
  five unverified/undated NSE mappings and five provisional runs with 19
  candles each. Both returned `PRAGMA integrity_check = ok` and no
  `foreign_key_check` violations.
- Existing focused tests: **64 passed, 1 skipped** across NSE reference
  validation, FYERS provisional staging, readiness, research foundation and
  backtest Part A. No code or new tests were needed to enforce these already
  fail-closed gates.
- Canonical `pilot.db` SHA-256 remained
  `c6c6266792b0293841c0163de9585192f5f637101dba9a046597d0e1e9a13850`;
  staging `provisional.db` remained
  `e9d815569f75038665d19131debbc950230cb2e639b5ef593be048d56ea12697`.
  The 19 ZIP hashes were verified in Stage D2.3; no ZIP, source database or
  virtual portfolio was modified in this stage.

**Final per-stock verdict:** RELIANCE, TCS, INFY, HDFCBANK and ICICIBANK all
remain **PROVISIONAL / NOT RESEARCH ELIGIBLE**. No real-data backtest, model
training, canonical promotion or trade was performed.
