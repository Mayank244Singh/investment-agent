# Stage D1 — synthetic quantitative research baseline

## Scope and reused components

This is one **synthetic-only** research experiment. It does not read NSE
ZIPs, FYERS staging, canonical prices, the production ledger or the isolated
virtual account. The Stage C2 virtual trading demonstration was verified in
the browser separately; it is not a research data source. All 95 FYERS
candles remain PROVISIONAL under the Stage B1.2 boundary. Stage B2.1/B2.2
still lack 14 independent NSE reference sessions, historical FYERS identity
evidence and point-in-time availability evidence.

The experiment reuses:

- `backtest.data.PriceData`, `Bar` and `HistoryView` for validated OHLCV,
  duplicate rejection and bounded completed history. The stored-price reader
  exists but is **not called**.
- `backtest.engine.simulate` for close-decision/next-open execution, risk
  validation, adverse slippage, volume caps, FIFO ledger, cash, stops and
  equity curves. Its result now exposes realised P&L per completed sale so
  win rate uses the same FIFO calculation, without a parallel lot matcher.
- `backtest.strategies._buy_quantity` for the same risk-aware sizing as the
  existing baselines, plus `buy_and_hold` for the comparison arm.
- `backtest.costs.CostProfile` and `charges` for integer-paise, half-up
  component charges. The dated, **unverified** placeholder rates are reused;
  Stage D1 specifies an additional illustrative 10 bps adverse slippage.
- `backtest.report.metrics` for net return, maximum drawdown, trades,
  turnover, mean exposure, costs and cost drag. Its no-annualisation rule
  applies because the evaluation has only 70 invented sessions.
- Phase 7A/7B chronological split and dataset gates remain unchanged. No ML
  fit, model promotion, real-data permit or evidence-based opportunity
  assessment is attempted here.

## Experiment contract

`SYNTHA` is an invented symbol. A fixed four-regime path generates 100
fictional weekday daily candles from 6 January through 23 May 2025. These
dates are declared synthetic sessions, **not an NSE holiday calendar**.
`SyntheticScenario` requires one bar per declared session, exact symbol,
ordered unique sessions, consistent previous closes and `origin="synthetic"`.
`Bar` and `PriceData` reject invalid OHLCV and duplicate symbol/date keys.
The command has no database or provider argument. The provisional FYERS
database and canonical price tables are never opened.

The first 30 sessions warm up the averages. At each completed close, the
10-session MA is compared with the 30-session MA using integer cross-products
(strictly greater means long; equal or lower means cash). Signals enter the
existing simulator queue and can fill no earlier than the following session's
open. There is no shorting or leverage. Sizing uses the project's 10% order,
20% symbol, 1% planned trade-loss and 2% daily realised-loss limits; the
simulator revalidates the actual fill. A 5% stop below the signal close is
required by the existing risk gate. It does **not** guarantee that loss.

The buy-and-hold arm decides at the same warm-up close and uses the same
risk-capped sizing, costs, slippage and simulator. It is not a fully invested
index proxy. Its safety stop did not trigger in this fixed scenario. Cash
holds the original ₹10,000 and makes no trades. All three arms are evaluated
over the same 70 sessions, 17 February through 23 May 2025.

## Reproduce

From this worktree, with the existing virtual environment:

```sh
PYTHONPATH=backend /Users/mayanksingh/Investment/investment-agent/.venv/bin/python \
  -m app.modules.backtest.trend_research --output-dir /tmp/stage-d1-review
```

Choose a fresh output directory; the command refuses to overwrite an
existing report. The JSON and Markdown are deterministic for the same code
and cost profile. They include SHA-256 hashes of the configuration and all
invented bars. The generated example reports are
`docs/reports/stage-d1-synthetic-report.json` and
`docs/reports/stage-d1-synthetic-report.md`. The JSON includes dated equity
curves, fills, fee components, metrics, warnings and the full cost profile.

## Observed synthetic result

| Arm | Final equity | Net return | Max drawdown | Trades | Win rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| MA 10/30 | ₹10,018.13 | 0.1813% | 0.1912% | 3 | 0% (1 closed sale) |
| Risk-capped buy and hold | ₹10,026.48 | 0.2648% | 0.3325% | 1 | undefined (open holding) |
| Cash only | ₹10,000.00 | 0% | 0% | 0 | undefined |

The trend arm paid 680 paise in illustrative charges and 297 paise of
slippage impact, for 977 paise cost drag. Its turnover was 286,839 paise
(28.6839% of opening capital) and mean invested exposure 6.0631%.
The buy-and-hold arm outperformed this trend path. These invented results
are a software check, **not evidence of an investable edge**.
Win rate is the fraction of completed sell fills with positive net FIFO
realised P&L. Open holdings and zero-sale arms have undefined win rate.

## Integrity checks and limits

Tests check future-bar isolation, warm-up, next-open fill, missing/duplicate
and invalid bars, stored-origin rejection, zero trades, costs, FIFO-derived
win rate, deterministic reports, and no network socket. The existing
backtest tests cover stop behavior, risk rejection and ledger consistency.

The cost profile is unverified and not a broker quotation. Daily OHLC does
not establish intraday event order, liquidity, fill probability or market
impact. The 100-session fictional path is too short for annualised metrics
or statistical conclusions. No real-data ML training, provisional-data
promotion, trading or broker access follows from this result.

Verification: the full backend suite reported **531 passed, 1 skipped**;
Ruff passed; `git diff --check` passed. Mypy passed on `backend/app` and
the relevant backtest tests (88 files), and on all backend files except the
pre-existing `test_virtual_trading.py` (122 files). The unrestricted
`mypy backend/app backend/tests` target reports 17 existing typing errors
in that untouched Stage C2 test file; no Stage D1 file has a mypy error.

The next research milestone should first close the separate real-data
identity, corporate-action, point-in-time availability and NSE reference
coverage gaps. A longer, legally usable history and a verified cost profile
are prerequisites for meaningful walk-forward comparison against multiple
simple baselines. Do not promote a model from this synthetic experiment.
