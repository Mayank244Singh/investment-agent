# Phase 7A research design: intraday, swing and long term

## Boundary and architecture

Provider observations → canonical market data → explicit point-in-time
validation → research features and labels → chronological research dataset.
The research package has no database writer, provider client, broker client,
model fitting or order path. Existing daily price tables, ZIP import, ledger,
risk validator and daily backtester remain independent. Phase 7A emits no
signals or investment recommendations. A future prediction or proposal must
still pass the unchanged deterministic risk gate before any execution path.

`StrategyFamily` names `intraday`, `swing` and `long_term`; `Timeframe` names
`1m`, `5m`, `15m` and `1d`. The daily `ResearchConfig` can select swing
(5/10 trading-session horizons) or long-term (60/120 sessions); a `None`
strategy retains the legacy combined daily dataset. Intraday uses a separate
`IntradayConfig` and 5/15/30/60 **elapsed-minute** horizons. No strategy
assumes an instrument is historically in today's universe.

Shared contracts: stable instrument ID, UTC decision and source-availability
times, provider provenance, adjustment/identity status, version, missing
feature indicators and rejection reasons. Daily data uses an explicit ordered
session calendar and caller-supplied decisions; intraday uses explicit
exchange-local session boundaries with holiday and special-session entries.
No calendar is inferred from observed prices. The intraday calendar is a
research input, **not** a maintained or certified NSE calendar.

## Intraday observation and decision convention

Each minute candle stores start and end as explicit UTC datetimes; local
session interpretation uses `Asia/Kolkata`. It carries integer-paise OHLC,
integer volume, completion flag, nullable availability time, provider/source
record key, adjustment status and identity verification. Duration must match
the timeframe; all OHLCV values and the exact session grid are validated.
Unknown availability stays unknown. A decision occurs at candle end plus a
configurable nonnegative lag. Only completed candles whose availability is
no later than that decision enter features. The dataset build time must be
after the decision and after all label-source availability. These timestamps
are supplied facts, never inferred from ingestion time.

Input order, duplicate instrument/timestamps, missing expected bars, holidays,
out-of-grid bars, incomplete candles and conflicting quality flags are
reported. Duplicate or out-of-order groups fail closed. A gap invalidates
samples that depend on the gap. Bars after the decision cannot change a
sample's features, session VWAP, running high/low or opening range. Futures
may change only that sample's supervised label.

## Feature formulas and warm-up

Ratios and indicators use `Decimal`; prices remain integer paise. No float
money or forward-filled candles are used. Missing warm-up inputs yield `None`
and a named missing feature. `required_features` config determines which
missing features make a sample unusable. The intraday default requires
`session_vwap_paise`; a zero-volume prefix cannot produce it.

- Intraday 1/5/15-minute return is close(t)/close(t−h)−1 at an **exact**
  earlier candle end. A 5- or 15-minute series cannot supply a 1-minute
  return; that feature stays missing. Momentum uses `momentum_bars` prior
  closes; rolling volatility is population standard deviation of
  `rolling_window_bars` completed close returns.
- SMA is arithmetic mean of the last `rolling_window_bars` closes. EMA uses
  that many bars for its initial SMA, then alpha = 2/(period+1).
  RSI(14) uses Wilder smoothing, 50 for a flat 14-change history, 100 when
  there are gains and no losses. MACD uses EMA(12)−EMA(26), a 9-value EMA
  signal and their difference. ATR(14) uses true ranges with the previous
  close, first bar high−low, then Wilder smoothing.
- Relative volume is the current completed bar's volume divided by the mean
  of the **previous** configured window of bars. Volume spike is 1 when
  that ratio is at least 2, otherwise 0. A zero previous mean yields `None`.
- Session VWAP is sum(((high+low+close)/3)×volume)/sum(volume) over the
  completed prefix **of that session**. Distance is close/VWAP−1. The
  intraday range uses running high and low divided by the first open.
  Opening-range high/low/breakout appear only after the configured opening
  interval has completed. Minutes from open/to close use the supplied
  session boundaries. All session-dependent calculations reset each day.
- Daily optional features add log return, 5/10/20/60-session returns,
  SMA(5/10/20/50), EMA(12/26), volatility(10/20), rolling 20-session
  maximum drawdown, 20-session high/low position, volume averages(5/20),
  relative volume, RSI(14), MACD(12/26/9) and ATR(14). Daily ATR and
  high/low position need optional OHLC. Legacy close/volume observations
  keep working and report those indicators missing. The existing daily
  feature and target defaults are preserved.

Indicators restart at a supplied intraday session boundary. That is a
conservative research convention, not a claim that it is the best trading
signal. Daily EMA is seeded at the start of the supplied contiguous history;
its value can vary with how much prior history the caller supplies.

## Targets, leakage and execution boundary

Intraday labels are close(t+h)/close(t)−1 and `int(return > 0)` for 5, 15,
30 and 60 elapsed minutes. A horizon must be divisible by the source bar
duration, contain every expected bar and end no later than the configured
exchange close. Overnight targets are refused until a separate reviewed
policy exists. Daily labels use the explicit session calendar and the same
close-to-close formula at 5/10/60/120 trading sessions. A research close
label is **not** executable P&L or a guaranteed fill at that close.

Chronological train/validation/test splitting purges samples whose label
reaches the next partition. An optional embargo removes observations after
each boundary. There is no random split, scaler, fitted imputer or trained
model. Future fitted transformations must learn only on training data and
be replayed unchanged in validation/test. Cross-sectional ranks will need
point-in-time universe membership and synchronized availability before use.

Invalid/missing prices, unverified identity or adjustment, unresolved
corporate actions, unknown availability and duplicate observations block
affected samples. These flags are caller-supplied and cannot independently
prove that actual prices were adjusted correctly. No split, bonus, dividend,
merger or ticker-change factor is invented.

## Research-only execution and risk contracts

`ResearchFeeSchedule` is dated and versioned, with separate brokerage, STT,
exchange, GST, SEBI and stamp rates; no rate is hardcoded. The optional
`IntradayExecutionContract` names spread, slippage, partial fills, liquidity,
latency, market impact and square-off assumptions. Unknown values stay
unknown. No execution simulator or fill path is implemented here, and daily
OHLCV alone cannot support a realistic spread or order-book simulation.
`IntradayRiskResearchContract` names per-trade/daily loss, position, open
positions, turnover, exposure, consecutive losses, cutoff, mandatory square
off and circuit-breaker limits. It does not change or bypass the production
risk validator and does not execute orders.

## Offline CLI and data needs

`research-validate --synthetic-json PATH` preserves the daily command.
`research-intraday-validate --synthetic-json PATH` reads a JSON file under
5 MB with `synthetic: true`, `dataset_version`, `timeframe`, `built_at_utc`,
`sessions`, optional `holidays`, and `candles`. Session objects need `date`,
`opens_local`, `closes_local`, optional `special`; candle objects need the
fields of `IntradayCandle` except `timeframe`, which comes from config.
Optional `--train-end-utc`, `--validation-end-utc` and `--embargo-minutes`
report chronological split sizes. Both commands print quality counts and
read no real database, credentials or provider. Example verification:

```bash
.venv/bin/python -m pytest backend -q
.venv/bin/ruff check backend
.venv/bin/mypy backend
PYTHONPATH=backend .venv/bin/python -m app.cli research-intraday-validate --synthetic-json /tmp/invented-intraday.json
```

Meaningful intraday research requires licensed, historically complete
1/5/15-minute OHLCV, verified exchange sessions including holidays/special
sessions, source availability, instrument identity history, corporate-action
semantics and usable spread/liquidity data for execution studies. Swing and
long-term research need years of licensed, adjusted daily OHLCV, historical
universe membership and action/identity lineage. The existing NSE ZIP
importer supplies a short **daily** unadjusted history. The current Upstox
adapter is also daily only; live access has returned HTTP 403 and intraday
coverage, retention and training rights are unverified. Neither is an
approved intraday training source. Phase 7B may add LightGBM/XGBoost only
after source rights, quality and point-in-time lineage are independently
established and reviewed. Synthetic tests make no profitability claim.
