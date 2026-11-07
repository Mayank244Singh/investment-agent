# Phase 7D.1: Point-in-time evidence foundation

## Scope and architecture

`backend/app/modules/research/evidence.py` is a pure, read-only research layer. It consumes caller-supplied synthetic or future approved observations; it has no database session, provider client, ledger, order, broker, or risk-gate dependency. The current flow is: verified exchange calendar and completed intraday candles → point-in-time candle assessment → typed evidence records → independent opportunity assessment. No API or trading path calls this module yet.

The module reuses Phase 7A's `ExchangeCalendar`, `ExchangeSession`, `IntradayCandle`, `StrategyFamily`, and `Timeframe`. Phase 7B.2B's readiness gate remains the separate prerequisite for a real dataset. Existing daily price tables, importers, valuation, backtesting, ledger, Profit Vault and `RiskValidator` are unchanged. The external Trader Skill repositories informed the completed-candle check but supplied no production code, probabilities or US-market assumptions.

## Contracts and Indian-market assumptions

`IndianInstrument` retains the stable internal instrument ID, exchange (`NSE` or `BSE`), normalized symbol, optional ISIN and an explicit identity-verification flag. An NSE record never matches a BSE record solely by symbol. Money, if later added to this layer, must be INR integer paise, with Decimal-safe input conversion; this phase calculates no money, allocation or executable P&L.

Each `Evidence` carries its instrument, kind (prediction, technical, fundamental, market regime, portfolio context or data quality), stance (supporting, contradicting, neutral or unknown), provider, source key, UTC observation/first-availability/decision timestamps, quality, method version, input lineage, usage-rights flag, timeframe and optional horizon. Horizons are typed integers matching Phase 7B: minutes for intraday (5/15/30/60, divisible by candle width) and trading sessions for swing (5/10) or long term (60/120). A prediction may carry a Decimal value and calibrated uncertainty, both optional. `None` means missing, not zero. This contract does **not** certify calibration or licensing merely because a caller sets a field; upstream gates and auditable lineage remain necessary. Fundamental and regime evidence are placeholders, not invented Indian disclosures or signals.

The candle validator accepts only caller-verified exchange sessions, including explicitly configured special sessions and holidays, and 1/5/15-minute bars. It checks exchange/session identity, the exchange-local time grid, completed status, duplicate and missing intervals, source availability by the decision cutoff, staleness, instrument identity and adjustment status. Decisions between 5/15-minute closes use the last completed grid slot. Phase 7A's candle constructor rejects invalid OHLC and negative volume. Bars ending after the cutoff are ignored. Delayed or unknown-availability bars cannot fill an interval. Candle assessments bind their instrument, timeframe and decision timestamp; a mismatch fails closed. The exchange label and calendar are still supplied by the caller, so an authoritative exchange-specific calendar and first-availability data are required from future approved sources; no market hours or holiday history are inferred here. Daily swing/long-term evidence can use the common evidence contract, but this phase does not adapt daily canonical prices into verified evidence.

## Assessment semantics

`assess_opportunity` separates prediction from technical, fundamental, regime, portfolio and quality evidence. It filters future observations and unavailable evidence, never pools predictions from different horizons, rejects duplicate evidence identities and returns supporting and contradicting records separately in deterministic order. Technical disagreement does not prove an ML prediction wrong and does not automatically reject it. Intraday assessments explicitly report when completed-candle validation is absent. A later adapter must still prove that a technical record was derived from the bound validated candles; the current contract does not establish that lineage by itself.

- `REJECT`: a deterministic invalid identity, exchange, cutoff, timeframe, source-quality or candle-integrity condition.
- `WAIT`: a necessary candle or evidence has not arrived, is incomplete, missing or stale. The assessment is still not actionable.
- `INSUFFICIENT_EVIDENCE`: no hard failure or wait, but missing evidence, rights, uncertainty, calibration or allocation prevents approval.
- `ACCEPT` and `ACCEPT_WITH_REDUCED_SIZE`: reserved contract states. This phase never emits them because calibrated statistical evidence and a reviewed sizing policy are absent.

The evaluator returns no proposed size, confidence percentage, expected net return or order. It neither selects a Champion model nor promotes a Challenger. `method_version` and `input_lineage` can carry versioned model provenance later, but no model registry or promotion state is changed here. It never calls or overrides the deterministic production risk gate. Any future allocation proposal must pass that independent gate before any execution integration. The existing risk rules and Profit Vault are untouched.

## Verification and limits

Synthetic tests cover valid NSE/BSE sessions, special sessions, holiday and exchange mismatch, incomplete/future/delayed/stale bars, off-grid bars, decisions between candle closes, duplicate and missing intervals, invalid OHLCV, agreement/disagreement, absent fundamentals and calibrated uncertainty, horizon separation, invalid identity, assessment-context mismatch, deterministic output, and a future-bar negative control. The module has no database write path; a sentinel ledger/Profit Vault file is unchanged during evaluation. These tests establish contract behavior only, **not** model skill or trading profitability.

Real use requires licensed point-in-time Indian data, stable historical identity and universe membership, verified NSE/BSE calendars, trustworthy source-availability timestamps, corporate-action handling, validated calibration, broker costs and a reviewed allocation policy. Upstox Historical Candle V3 and LTP V3 remain blocked by HTTP 403, and data-use rights are unresolved. No live provider call or real-data model training was made for this phase. The nine Phase 7B model families may eventually emit versioned prediction evidence through a separately reviewed adapter; this module does not train or invoke them.

## Next milestone

Acquire legally usable historical Indian equity data from an alternative approved provider. Verify retention and ML-training rights, instrument mapping, corporate actions, authoritative NSE/BSE calendars, source availability timestamps and historical coverage before proposing model adapters or more ML features. Allocation previews and shadow trading require separate milestones and approval.
