# ADR 0004: Manual end-of-day EQ price data

- Status: Accepted for the observed profile only
- Date: 2026-11-02

## Context

Phase 4 adds local end-of-day valuation without automatic market-data access. Five manually supplied October 2026 NSE cash-market bhavcopy ZIP files showed the same layout, but no official format specification was available. This profile is **observed, not verified against an official specification**. The user is responsible for reading and complying with the data source's terms before using a file.

## Supported profile and price meaning

The single accepted file name is `BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip`, with a valid calendar date and exactly one ZIP member named the same without `.zip`. The CSV must be UTF-8 (a BOM is accepted), with these exact headers in this order:

```text
TradDt, BizDt, Sgmt, Src, FinInstrmTp, FinInstrmId, ISIN, TckrSymb, SctySrs, XpryDt, FininstrmActlXpryDt, StrkPric, OptnTp, FinInstrmNm, OpnPric, HghPric, LwPric, ClsPric, LastPric, PrvsClsgPric, UndrlygPric, SttlmPric, OpnIntrst, ChngInOpnIntrst, TtlTradgVol, TtlTrfVal, TtlNbOfTxsExctd, SsnId, NewBrdLotQty, Rmks, Rsvd1, Rsvd2, Rsvd3, Rsvd4
```

`TradDt` and `BizDt` must both equal the file-name date; `Sgmt=CM`, `Src=NSE`, and `FinInstrmTp=STK`. `ClsPric` is **assumed to be the official closing price**. On 2026-11-02, its 2026-10-30 values for INFY (₹1,185.00) and TCS (₹2,502.60) were spot-checked against the date-stamped final prices indexed from [NSE's closing-auction page](https://www.nseindia.com/market-data/closing-auction-session). This check supports those two values; it does not replace an official definition of `ClsPric`. The user will verify that definition manually before relying on the data.

## Storage and import decisions

Only `SctySrs=EQ` rows are stored. Other series are counted and reported by series because the portfolio ledger currently represents cash-equity holdings and has no safe mapping for other instruments. The `instruments.series` database `CHECK` constraint permits only `EQ`; adding any series requires a new migration and explicit valuation rules.

Instruments are keyed by trimmed, uppercase symbol plus series, matching ledger symbols. An ISIN change for the same key stops an import. A ticker change creates a different instrument and **splits its price history**; no automatic ticker mapping or history merge is inferred. The integrity command reports missing or malformed ISINs, including a failed ISO check digit, but cannot prove that a valid ISIN belongs to a symbol without a trusted external source.

Prices are parsed with `Decimal`, reject more than two decimal places, and are stored as integer paise. ZIP members are read in memory without extraction or network calls. File names, members, headers, dates, rows, duplicate keys, and price ranges are checked strictly; the uncompressed CSV is capped by validated configuration (50 MiB by default). The default import is all-or-nothing in one database transaction. `--skip-bad-rows` reports rejected EQ rows by line; non-EQ rows are reported as skipped. A repeated SHA-256 file hash is a no-op. A changed price for an existing symbol and date or a changed ISIN stops the whole import, and prices are never silently overwritten. Each stored price references its import batch, which records source label, file name, hash, counts, status, and UTC import time.

The application does **not** download, scrape, or contact a source for prices. The user downloads one file manually from a source whose terms they have reviewed and keeps it outside the repository. Tests use invented symbols and synthetic CSV rows only.

## Valuation and integrity

For an as-of date (default today in `Asia/Kolkata`), valuation uses the latest EQ close on or before that date. A price is usable through five calendar days by default; the positive `PRICE_STALENESS_DAYS` setting is validated at startup. Missing or older prices produce `Unavailable` with a reason and last known on-or-before date, never an estimate. Market value is held quantity times close in paise; unrealised P&L is market value minus remaining FIFO cost. Priced market value and unrealised P&L are totaled separately from unpriced holdings' cost. Risk capital and all risk rules remain at FIFO cost.

The read-only integrity command opens SQLite with `mode=ro` and checks price/instrument links, positive OHLC and previous close, OHLC ranges, non-negative volume, duplicate instrument/date keys, future dates, batch row-count reconciliation, and missing or inconsistent stored ISINs. It reports every discrepancy and exits non-zero.

## Known limitations

- Prices are **unadjusted** for splits, bonuses, and dividends. They must not be used to calculate returns across those events until corporate actions are handled.
- No live or intraday feed, automatic download, adjustment, or source-license verification exists.
- The observed format may change. Any different headers or ZIP layout are rejected until a new reviewed profile is approved.
- `ClsPric` meaning remains an assumption pending the user's manual verification against an official specification.
- ISIN checks validate stored syntax/check digit, not ownership or ticker-change history; a ticker change splits history.
- Calendar-day staleness can mark a price unavailable over a long market closure; there is no trading-calendar adjustment.
- Partially priced portfolios have no complete market-value total. Unpriced FIFO cost is shown separately and is never treated as zero.
- Daily risk loss remains realised-only, and risk capital remains at FIFO cost rather than market value.
