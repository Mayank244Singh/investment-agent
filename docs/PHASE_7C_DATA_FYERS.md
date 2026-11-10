# Phase 7C-Data: FYERS history adapter (offline implementation)

## Scope and status

The adapter is an isolated, read-only parser/client for FYERS API v3 `GET /data/history`. It has **not** been called with a real token. No FYERS candle has been downloaded, saved, reconciled with NSE, or admitted to ML training. The existing NSE ZIP importer, Upstox adapter, canonical `daily_prices`, ledger, risk gate, strategies, and Champion–Challenger framework are unchanged.

The five-stock pilot is bounded to RELIANCE, TCS, INFY, HDFCBANK and ICICIBANK. Its FYERS tickers and ISINs must be checked against a contemporaneous FYERS NSE Capital Market symbol master and the existing internal instrument IDs before any mapping is marked verified. A current master does **not** prove historical ticker validity or preserve delisted constituents. BSE remains a future extension; the current adapter deliberately rejects non-NSE-EQ ticker formats.

## Contract and data handling

- FYERS documents the data host `https://api-t1.fyers.in`, `GET /data/history`, `symbol=NSE:...-EQ`, `resolution=D|1|5|15`, `date_format=1`, `range_from`, `range_to`, and six-field candles `[epoch, open, high, low, close, volume]`. The adapter restricts one request to 366 calendar days for daily bars or 100 for minute bars. The connectivity CLI tightens this to five days and one GET.
- Authentication is `Authorization: app_id:access_token`, **not** Bearer. `FYERS_APP_ID` comes from the local process environment. The token is read only when making a request from `~/fyers-auth/.access_token` (or an explicitly supplied private path). The file must be a regular file owned by the current user with no group/other permissions; use `chmod 600 ~/fyers-auth/.access_token`. The token is never read by `--help` or a dry run. It is never stored in the repository, database, tests or logs. FYERS says access tokens expire daily at 6:30 AM; renewal is a manual prerequisite.
- The transport uses one HTTPS GET, 10-second timeout, 2 MB body cap, no proxies, no redirects, and zero retries. HTTP 401, 403, 429 and other errors receive fixed diagnostics. Provider response messages and authorization headers are never printed.
- JSON numbers are parsed with `Decimal`; prices pass the shared `decimal_paise` rules and are held as integer paise. Rows must have positive OHLC within the low/high range, nonnegative integer volume, valid in-range epoch times and strictly increasing unique timestamps. Missing rows are **not** inferred to be bad without a verified exchange calendar: they could be holidays or trading halts.
- Epoch times are converted to explicit UTC and Asia/Kolkata trade dates. A daily candle for the current India date is conservatively incomplete. A minute candle is complete only after its fixed interval ends; the Phase 7D.1 evidence contract must additionally validate it against a verified NSE session, including special sessions and holidays. Provider first-availability time is unknown and remains `None`, so point-in-time evidence stays deferred. Corporate-action adjustment status remains unverified.
- `verify_master_mapping` compares a supplied symbol-master row (`exSymbol`, `symTicker`, `exSeries`, `exchangeName`, `isin`) with the existing internal instrument and requires an independently reviewed historical validity interval. A current master alone cannot establish that interval. Only explicitly verified, date-covered mappings can become provider-neutral daily `NormalizedObservation` values. This does not write any observation or select a canonical bar. Future authorized ingestion must use `record_mapped_observation` and explicit reconciliation; no conflict may overwrite an NSE canonical price.

## First separately approved connectivity check

After checking FYERS Market Data permission and the ticker in its symbol master, and after receiving explicit approval for **one** read-only request, run from the project root:

```sh
export FYERS_APP_ID='<your FYERS App ID>'
PYTHONPATH=backend .venv/bin/python -m app.cli check-fyers-history \
  --symbol RELIANCE --ticker '<verified FYERS NSE EQ ticker>' \
  --from-date 2026-10-26 --to-date 2026-10-30 \
  --timeframe 1d --allow-live --confirm-read-only
```

Without both switches, the command makes no request and does not read the token. A successful call reports the HTTP status, candle count, complete-candle count and dates only. It does not expose OHLCV, save a file or modify a database. It reports identity as unverified because this connectivity command alone cannot validate the FYERS master/ISIN against the internal instrument. **Do not run it as part of automated tests or this phase without separate authorization.**

## Access and licensing blockers

FYERS documents Market Data permission, history window and plan limits. Its [API rate-limit guide](https://support.fyers.in/portal/en/kb/articles/what-are-the-fyers-api-rate-limits-for-standard-and-prime-plans) lists Standard Data API limits of 5 requests/second, 50/minute and 5,000/day; Prime has higher limits. The current CLI makes at most one request and never retries a 429.

The [FYERS API terms](https://fyers.in/terms-and-conditions-api) restrict exchange-data use for charting, technical tools, paper trading, gaming and virtual trading, and require written consent for some copying or redistribution uses. They do not explicitly grant this project's proposed long-term storage, backtesting or ML-training rights. **Do not ingest, retain or train on FYERS market data until FYERS/exchange permissions for the intended private research use are clarified in writing.** A successful connectivity response establishes only API access, not data rights or research suitability.

## Official references

- [Historical Data](https://fyers.in/api-docs/v3/api/market-data/historical-data)
- [API Standards and authorization header](https://fyers.in/api-docs/v3/api/api-standards/)
- [Individual-app token expiry](https://fyers.in/api-docs/v3/api/authentication/individual-app/)
- [Symbol Master JSON fields](https://fyers.in/api-docs/v3/api/market-information/symbol-master/symbol-master-json/)
- [Equity symbol format](https://fyers.in/api-docs/v3/api/appendix/symbology-format/)
- [FYERS-maintained market-data host reference](https://github.com/FyersDev/fyers-skills/blob/master/skills/fyers-trading/references/market-data.md)

## Remaining validation

1. Written clarification of storage, backtesting, technical-indicator and ML-training rights; account/app Market Data permission and plan entitlement.
2. Separately authorized single-request connectivity test, if rights permit that diagnostic.
3. Independently verified five-symbol FYERS master/ISIN mapping and historical ticker review.
4. Later, a temporary-database NSE overlap reconciliation and calendar/availability validation before any dataset eligibility or model training. There is no automatic bulk download path in this phase.
