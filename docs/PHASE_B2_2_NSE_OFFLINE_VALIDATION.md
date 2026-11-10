# Stage B2.2 — manual NSE references and offline validation

Status: validator implemented; 14 NSE session ZIPs still missing as of
9 November 2026. No NSE files were downloaded and no FYERS or NSE API call
was made in this stage. The existing 95 FYERS candles remain `PROVISIONAL`.

## Official acquisition options and rights

NSE's [All Reports](https://www.nseindia.com/all-reports) page lists
**CM-UDiFF Common Bhavcopy Final (zip)** under the equities reports. It
provides a date selector. The page says older CM bhavcopy CSV variants were
discontinued and directs readers to this ZIP report. The five locally held
examples match the observed
`BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip` profile, but the application
still lacks an official specification for the 34 CSV columns or a verified
definition of `ClsPric`. A date's report must be checked in the official UI;
the filename pattern is not proof that a particular file is available.

NSE's current [Terms of Use](https://www.nseindia.com/static/nse-terms-of-use)
prohibit systematic or automated data collection on its website. The
[NSE data-use policy](https://www.nseindia.com/static/market-data/nse-data-policy)
describes use-specific agreements for data subscribers and research access;
it does not, by itself, grant this project permission to automate website
downloads, store a broader dataset or use it for ML. No authorized automated
ZIP endpoint was established, so **no downloader was implemented**. For a
licensed feed, consult the official
[EOD/historical data subscription page](https://www.nseindia.com/static/market-data/eod-historical-data-subscription)
and get the exact permitted delivery and use terms in writing before any
automated acquisition is proposed.

## Files to obtain manually

Open [NSE All Reports](https://www.nseindia.com/all-reports) yourself. In the
equities/CM reports, choose each date below and select **CM-UDiFF Common
Bhavcopy Final (zip)**. Download one file per date through the provided UI.
Do not use a script, guessed URL, cookie replay, third-party downloader or
access-control workaround. If a date or ZIP is unavailable, record that and
stop; do not substitute another report format.

| Date | Expected filename, to confirm after download |
| --- | --- |
| 2026-10-12 | `BhavCopy_NSE_CM_0_0_0_20261012_F_0000.csv.zip` |
| 2026-10-13 | `BhavCopy_NSE_CM_0_0_0_20261013_F_0000.csv.zip` |
| 2026-10-14 | `BhavCopy_NSE_CM_0_0_0_20261014_F_0000.csv.zip` |
| 2026-10-15 | `BhavCopy_NSE_CM_0_0_0_20261015_F_0000.csv.zip` |
| 2026-10-16 | `BhavCopy_NSE_CM_0_0_0_20261016_F_0000.csv.zip` |
| 2026-10-19 | `BhavCopy_NSE_CM_0_0_0_20261019_F_0000.csv.zip` |
| 2026-10-21 | `BhavCopy_NSE_CM_0_0_0_20261021_F_0000.csv.zip` |
| 2026-10-22 | `BhavCopy_NSE_CM_0_0_0_20261022_F_0000.csv.zip` |
| 2026-10-23 | `BhavCopy_NSE_CM_0_0_0_20261023_F_0000.csv.zip` |
| 2026-11-02 | `BhavCopy_NSE_CM_0_0_0_20261102_F_0000.csv.zip` |
| 2026-11-03 | `BhavCopy_NSE_CM_0_0_0_20261103_F_0000.csv.zip` |
| 2026-11-04 | `BhavCopy_NSE_CM_0_0_0_20261104_F_0000.csv.zip` |
| 2026-11-05 | `BhavCopy_NSE_CM_0_0_0_20261105_F_0000.csv.zip` |
| 2026-11-06 | `BhavCopy_NSE_CM_0_0_0_20261106_F_0000.csv.zip` |

20 October was a reviewed NSE holiday; weekends are excluded. Keep the
original ZIP bytes in a private directory outside Git, separate from the
five existing files at `/Users/mayanksingh/Investment/sample-data/`. Do not
rename or overwrite a prior version. Capture the official source page or
download URL (without credentials), UTC acquisition time and `shasum -a 256`
for each file in a private supplemental JSON manifest. Its format is:

```json
{
  "sources": [
    {
      "trade_date": "2026-10-12",
      "filename": "BhavCopy_NSE_CM_0_0_0_20261012_F_0000.csv.zip",
      "sha256": "<64 lowercase hex characters from the actual file>",
      "source_url": "https://www.nseindia.com/all-reports",
      "acquired_at_utc": "<ISO 8601 UTC time of manual acquisition>"
    }
  ]
}
```

Add one source record per downloaded date. The validator requires an
official NSE HTTPS host and UTC acquisition time for each supplemental
record. Keep the manifest private and outside Git. The five existing hashes
remain in `historical-identity-evidence.json`; do not edit that file merely
to run this check.

## Offline command and acceptance

From the worktree root, after manually supplying some or all of the ZIPs:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend \
  /Users/mayanksingh/Investment/investment-agent/.venv/bin/python \
  -m app.cli validate-nse-references \
  --canonical-database /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/pilot.db \
  --staging-database /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/provisional.db \
  --calendar-json /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/nse-calendar.json \
  --evidence-json /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/historical-identity-evidence.json \
  --reference-dir /Users/mayanksingh/Investment/sample-data \
  --reference-dir /path/to/private/new-zips \
  --supplemental-manifest /path/to/private/supplement.json
```

Omit the second `--reference-dir` and `--supplemental-manifest` until new
files exist. The command opens both databases with SQLite `mode=ro` and
`query_only`, checks their integrity and unchanged byte hashes, and makes no
network call. It validates every supplied ZIP's manifest SHA-256, size,
single member, filename/date, ordered headers, date/segment/source fields,
all `EQ` rows, target ISINs, prices, volume and duplicate keys using the
existing strict parser. It compares signed FYERS-minus-NSE OHLC paise and
integer-volume differences, and refuses to silently prefer one provider.
Output is JSON with file statuses, missing dates, comparison counts and
conflicts. Exit 0 means all 19 files and 95 comparisons are valid/exact;
exit 1 means incomplete or conflicting references; exit 2 means invalid
inputs or database state. `ready_for_reference_review=true` means only
complete **reference reconciliation**. `research_eligible` is always false.
No verified mapping, canonical price, model dataset or trade is created.

The first read-only run with the five existing files returned exit 1:
5 validated files, 25 `MATCH`, 70 `NO_NSE_REFERENCE`, 14 missing dates,
both database hashes unchanged. Tests use invented symbols and temporary
databases only. Historical FYERS ticker identity, corporate-action basis,
first-availability time, broader NSE use rights and sufficient research
history remain independent blockers even if future OHLCV values match.
