# Phase 7B.2B: Historical market-data readiness

## Scope

This is a read-only, provider-neutral assessment of evidence needed for
intraday (1, 5 or 15 minute), swing (daily) and long-term (daily plus future
fundamental) research. It does not download prices, alter canonical bars,
adjust for corporate actions, or enable real-data ML training. Phase 7B.1's
training gate continues to accept only its trusted synthetic fixtures.

The assessment reuses `SourceObservation`, `InstrumentMapping`, immutable
ingestion provenance, `StrategyFamily`, `Timeframe`, `ExchangeCalendar` and
`ExchangeSession`. The existing NSE ZIP importer and Upstox offline pilot
are unchanged. The SQLite adapter opens the supplied file with `mode=ro`,
sets `query_only`, reads a snapshot and never runs migrations. It reads the
Phase 6A observation tables. There is no production intraday table, so an
intraday report over the current database correctly has zero candles; daily
bars are never relabelled as minute bars.

## Read-only report

From the project root, point to an explicitly selected SQLite file. A
disposable copy is preferable while reviewing results:

```sh
PYTHONPATH=backend .venv/bin/python -m app.cli data-readiness \
  --database /path/to/copy.db --strategy swing --timeframe 1d \
  --symbols INFY
```

Use `--strategy intraday --timeframe 1m` (or `5m`/`15m`) or
`--strategy long_term --timeframe 1d` for the other families. `--provider
upstox` restricts the observation scan and marks access blocked based on
the unresolved HTTP 403 diagnostics. An unfiltered report containing
Upstox observations also marks access blocked. The command exits 0 only
for `READY_FOR_RESEARCH`, 1 for an ineligible report, and 2 for an invalid
input/database. It prints no credential or raw provider payload.

The JSON contains the registered EQ instruments, observed instrument count,
per-symbol date ranges, session and row counts, providers, source provenance,
missing-session/candle counts, duplicate and mixed-provider counts, all
eligibility statuses, next actions, and up to 20 quality examples with the
full issue count. A `null` missing count means the calendar is unknown;
it is **not** zero missing bars. `training_enabled` is always `false`.

An optional `--calendar-json /path/to/reviewed-calendar.json` supplies
explicit, ordered exchange sessions, holidays, special sessions and halt
intervals. It must have exactly these top-level keys:

```json
{
  "source_note": "Operator-verified calendar source and version",
  "sessions": [
    {"date": "YYYY-MM-DD", "opens_local": "09:15:00",
     "closes_local": "15:30:00", "special": false}
  ],
  "holidays": ["YYYY-MM-DD"],
  "halts": [
    {"start_utc": "YYYY-MM-DDTHH:MM:SS+00:00",
     "end_utc": "YYYY-MM-DDTHH:MM:SS+00:00"}
  ]
}
```

The example shows the **file format**, not an assertion that those hours or
dates were valid NSE sessions. The operator must verify the source and full
date range. The engine excludes explicitly listed holidays and halt-covered
candle slots, including special-session hours. It never assumes every
weekday is a trading day or infers a halt from a missing candle. Without a
calendar, missing sessions and candles cannot be measured reliably.

## Eligibility criteria

The default minimum is 504 observed trading sessions per instrument for
intraday and swing, and 756 for long-term. These are conservative research
screening thresholds (roughly two and three trading years), not a claim of
statistical power or profitability. `--min-sessions` may set a stricter or
experimental threshold; the report records the effective threshold in its
reasons when coverage fails. Each expected candle must be present for every
instrument in the supplied verified calendar, apart from documented
holidays and halt intervals. Intraday candles must use explicit UTC start,
end and availability times, match the timeframe and active session grid,
and be complete. Daily availability must follow the verified session close.

OHLC prices must be positive integer paise with open and close inside the
low/high range; volume must be a nonnegative integer. Repeated observations
from one provider in the same instrument/candle slot are discrepancies.
Different providers may agree in a slot and are counted separately; a price
or volume disagreement fails quality. Each slot also needs exactly one
selected canonical observation; an agreement is retained as separate
provenance. Stored selected observations must link to the canonical row
with matching instrument, date and OHLCV values. Instrument identity requires one
verified time-valid provider mapping and an ISIN. Unknown or malformed
event, retrieval and availability timestamps are not promoted to known
point-in-time facts. Future-dated observations fail quality.

The result reports **all applicable** statuses:

| Status | Meaning |
| --- | --- |
| `INSUFFICIENT_COVERAGE` | Too few sessions, missing verified-calendar candles, or required long-term fundamentals absent. |
| `DATA_QUALITY_FAILURE` | Invalid OHLCV, timestamp, identity, duplicate or conflicting provider evidence. |
| `CORPORATE_ACTION_UNVERIFIED` | Adjustment/no-action evidence is missing for at least one observation. |
| `POINT_IN_TIME_UNVERIFIED` | Calendar, historical universe membership or source availability is unknown. |
| `LICENSING_UNVERIFIED` | Retention and ML-training rights are not documented. |
| `PROVIDER_ACCESS_BLOCKED` | The selected/observed Upstox source remains blocked. |
| `READY_FOR_RESEARCH` | None of the above applies to the assessed evidence. It does **not** authorize real-data training or live trading. |

The current database schema has no verified corporate-action adjustment
metadata, historical-universe membership or licensing record. The CLI
therefore never asserts those claims from row counts or a filename; stored
data remains ineligible. Pure synthetic tests exercise the positive result
using explicit invented evidence. A later, separately approved design must
establish trusted evidence sources and an independent real-data training
permit before any real-model fit.

## Provider status and next evidence

| Source | Documented/implemented capability | Verified working here | Unresolved |
| --- | --- | --- | --- |
| NSE manual ZIP | Strict **observed** EQ daily file profile; unadjusted canonical storage and immutable observations. | Five manually supplied files imported and checked; profile was not verified against an official specification. | Original publication/availability time, corporate actions, historical universe and data rights. Legacy reconstructed rows keep those timestamps unknown. |
| Upstox | Official documentation describes Historical Candle V3 and Analytics Token access; the bounded offline adapter parses and reconciles provider observations. | Synthetic offline adapter tests only. Separately authorized Historical Candle V3 and LTP V3 diagnostics returned HTTP 403. | Live access, historical coverage, adjustment semantics and retention/ML-training rights. |
| Future provider | The observation and time-valid mapping contracts accept another approved source. | No provider-specific implementation or real reconciliation. | API contract, identity, provenance, availability and licensing must be reviewed separately. |

See ADR 0006 and the Phase 6B.2 pilot report. No provider is endorsed as a
production research feed here.

Before real-model training: obtain and document data rights; resolve provider
access; acquire a long, licensed history; verify adjustment and corporate
action lineage; obtain an authoritative historical session/holiday/halt
calendar and point-in-time universe; verify instrument identities and source
availability; reconcile provider differences; then seek separate approval
for a real-data dataset and training gate. Intraday additionally needs
historically complete 1/5/15-minute bars and session metadata; long-term
also needs point-in-time fundamentals. No broker request or order is part
of this phase.
