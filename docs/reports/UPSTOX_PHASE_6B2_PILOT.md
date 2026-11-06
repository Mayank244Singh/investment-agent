# Phase 6B.2 Upstox live pilot: blocked by authentication

- Test: 2026-11-05, approximately 14:05 UTC / 19:35 Asia/Kolkata. The exact request instant was not persisted.
- Decision: **NO-GO for a larger historical pilot.** The first authenticated Historical Candle V3 GET was denied. Stop condition applied; no further candle requests were made.
- Reference: five common NSE EQ sessions, **2026-10-26 through 2026-10-30**, 25 canonical NSE bars in total, read from the application database using SQLite `mode=ro`.
- Credential handling: the private Analytics Token was loaded from the git-ignored `.env` in process memory. Its value, length, prefix, suffix and request header were not printed or stored. No frontend code received it.

## Verified documentation and identity

The [official V3 endpoint](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/) is a GET at `/v3/historical-candle/{instrument_key}/days/1/{to_date}/{from_date}` for daily candles. Dates use `YYYY-MM-DD`; the documented daily range maximum is one decade. Its documented success body is `status` plus `data.candles`, with timestamp, open, high, low, close, volume and open interest in that order. The [Analytics Token documentation](https://upstox.com/developer/api-documentation/analytics-token/) lists Historical Data GETs as supported without a whitelisted static IP, but this local token's access was **not confirmed**. The [standard API limits](https://upstox.com/developer/api-documentation/rate-limiting/) include historical candles; this pilot used one attempt per symbol, no retry and 1.1 seconds between successful requests. No pagination is documented for this endpoint.

The official [NSE instrument master](https://upstox.com/developer/api-documentation/instruments/) supplied one `NSE_EQ`, `EQ`, `NSE` record for each symbol, with a matching ISIN and an `NSE_EQ|ISIN` key. The master was fetched twice without a credential (one preflight identity check, one CLI run); it was not saved to the repository.

| Stock | Internal ID | Matched ISIN | Verified Upstox key | NSE bars | Upstox bars obtained | Exact | Price mismatches | Volume mismatches | Missing sessions |
|---|---:|---|---|---:|---:|---|---|---|---|
| RELIANCE | 2008 | INE002A01018 | NSE_EQ\|INE002A01018 | 5 | — | Not tested | Not tested | Not tested | Not tested |
| TCS | 2385 | INE467B01029 | NSE_EQ\|INE467B01029 | 5 | — | Not tested | Not tested | Not tested | Not tested |
| INFY | 1137 | INE009A01021 | NSE_EQ\|INE009A01021 | 5 | — | Not tested | Not tested | Not tested | Not tested |
| HDFCBANK | 946 | INE040A01034 | NSE_EQ\|INE040A01034 | 5 | — | Not tested | Not tested | Not tested | Not tested |
| ICICIBANK | 1029 | INE090A01021 | NSE_EQ\|INE090A01021 | 5 | — | Not tested | Not tested | Not tested | Not tested |

## Execution and safety

The CLI copied the selected NSE reference rows into a fresh temporary SQLite database. The first historical GET resulted in an authentication/permission denial. The client currently maps HTTP 401 and 403 to one safe message, so the exact status cannot be reconstructed from this run. It made **1 authenticated historical GET**, **0 retries**, and obtained **no usable real Upstox candle response**. No discrepancy, missing-session or corporate-action classification is possible. No provider observation was written; idempotency could only be checked by automated mock tests. The temporary database was deleted after the failed run.

Subsequent separately authorized diagnostics distinguished the status: Historical Candle V3 and LTP V3 each returned HTTP 403. This does not establish the authorization root cause. The live pilot remains opt-in, with no automatic retries, fallback broker requests or broker execution. It is not a production-ready market-data source.

The real application database SHA-256 was equal before and after the attempt. Canonical NSE prices, portfolio, ledger, risk, valuation and backtesting behavior were not changed. No order or trading endpoint was called. The working tree was left dirty as found; no commit or push was made.

Backend checks after the attempt: **246 passed, 1 skipped**, Ruff clean, mypy clean (77 source files). Frontend: **31 passed**, lint and production build clean. The 17 test warnings are pre-existing dependency/deprecation warnings.

## Rights and next decision

The [general Upstox terms](https://upstox.com/terms-of-use-and-privacy-policy/) and public API pages reviewed here did not establish unrestricted rights to retain historical market data, redistribute it or train ML models on it. This is an unresolved permission question, not a claim that those uses are forbidden. There is also no evidence yet about Upstox price adjustment semantics versus the unadjusted NSE ZIP bars; no corporate-action adjustment was attempted.

Before another live request, the operator should verify that the Analytics Token is active for this account and that Historical Candle V3 is permitted, without sharing the token. Once access is fixed, rerun this same bounded five-stock pilot on a new temporary database. Resolve any data differences and retention/training rights before considering a larger download. Do not start Phase 6C or ML training from this result.
