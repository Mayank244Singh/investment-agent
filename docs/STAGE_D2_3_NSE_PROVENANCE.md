# Stage D2.3 — NSE reference acquisition provenance

## Scope and existing requirement

The Stage D2.2 audit identified 19 NSE CM-UDiFF ZIPs covering the expected 12 October–6 November 2026 sessions. Five files (26–30 October) were already registered in `historical-identity-evidence.json`; the 14 files below lacked the `source_url` and `acquired_at_utc` fields required by the existing strict `validate-nse-references` CLI. This stage did not change the parser, validator, ZIPs, canonical prices, or provisional candles.

The existing supplemental manifest format is `{"sources": [{"trade_date", "filename", "sha256", "source_url", "acquired_at_utc"}]}`. The private manifest was created at `/Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/nse-reference-supplement-20261109.json` with permission `0600`; it is outside Git. It contains the exact file-specific acquisition URLs and UTC timestamps. The SHA-256 values below were recomputed from each original ZIP and matched the Stage D2.2 audit.

## Evidence and confidence

Each of the 14 ZIPs has macOS `com.apple.metadata:kMDItemWhereFroms` containing an `https://www.nseindia.com/api/reports` URL. Its query date matches the ZIP trading date; its report name is `CM-UDiFF Common Bhavcopy Final (zip)`, with capital-market, equities and single-file parameters. The same file has `com.apple.metadata:kMDItemDownloadedDate` with the UTC download time below and a Safari quarantine marker. These are **recorded, file-specific local acquisition facts**. They were checked against the ZIP name, its content hash and the validator's existing provenance schema. No browser-history database was needed.

Confidence is **high for the locally recorded Safari download source and time**, but this is not a cryptographic attestation from NSE. Extended attributes can be copied or altered. The URL is an acquisition URL, not evidence of the exchange's publication time or a grant of data-use rights. Filesystem modification and creation times were not used as acquisition times.

## Per-file provenance

All rows have the same evidence type: file-specific Safari download URL and UTC download-date metadata, corroborated by the Safari quarantine marker. The exact URL for each file is retained in the private manifest. “None” means no field remains missing under the strict validator's acquisition-provenance contract.

| Trading date | Original ZIP | SHA-256 | Recorded UTC download time | Status and confidence | Missing validator fields |
| --- | --- | --- | --- | --- | --- |
| 2026-10-12 | `BhavCopy_NSE_CM_0_0_0_20261012_F_0000.csv.zip` | `818f673b3cdb96ae8ec4e466c7c4fd3a28d035482173a675dd24f9437a3ecd99` | 2026-11-09T18:17:47.145017+00:00 | Recorded in private supplement; high local confidence | None |
| 2026-10-13 | `BhavCopy_NSE_CM_0_0_0_20261013_F_0000.csv.zip` | `b3aba7f1e20587e4c77a5de2b720cbcd5d8f4c8fe42e1b67f06f0d7deaabbf46` | 2026-11-09T18:18:32.571906+00:00 | Recorded in private supplement; high local confidence | None |
| 2026-10-14 | `BhavCopy_NSE_CM_0_0_0_20261014_F_0000.csv.zip` | `db0b3819da45da615d3aa5ac7888a2b0670189d73f8d33f1e377146c89d9c94f` | 2026-11-09T18:18:43.825513+00:00 | Recorded in private supplement; high local confidence | None |
| 2026-10-15 | `BhavCopy_NSE_CM_0_0_0_20261015_F_0000.csv.zip` | `3d1c7039e2c70868cc44b51d1f9e4b0f942afc0575fe8c4a524aed9e01eac3e8` | 2026-11-09T18:18:51.825633+00:00 | Recorded in private supplement; high local confidence | None |
| 2026-10-16 | `BhavCopy_NSE_CM_0_0_0_20261016_F_0000.csv.zip` | `d2c4406c1ecbfbcb9acdafa424f921caad7467d97da47c65973b34aa0aac70bc` | 2026-11-09T18:18:58.402827+00:00 | Recorded in private supplement; high local confidence | None |
| 2026-10-19 | `BhavCopy_NSE_CM_0_0_0_20261019_F_0000.csv.zip` | `f0a2e9bf7cb185bb112704355772d2954d662abee3d7295414aabc26d6ecfbde` | 2026-11-09T18:19:31.100938+00:00 | Recorded in private supplement; high local confidence | None |
| 2026-10-21 | `BhavCopy_NSE_CM_0_0_0_20261021_F_0000.csv.zip` | `7d19ef4c8f7e3bf80828a7a33c4fe7d2cd8a56cbecc5e2ce42ce81a35e2ba243` | 2026-11-09T18:19:58.980177+00:00 | Recorded in private supplement; high local confidence | None |
| 2026-10-22 | `BhavCopy_NSE_CM_0_0_0_20261022_F_0000.csv.zip` | `4e1e097210a023fa6a2bfd67610528d28ac0307c6417c8c85cfa4b01196d37b2` | 2026-11-09T18:20:06.159080+00:00 | Recorded in private supplement; high local confidence | None |
| 2026-10-23 | `BhavCopy_NSE_CM_0_0_0_20261023_F_0000.csv.zip` | `009b57f15cb36e14fa956b2e585b69b23f279eb8532c2c63c0adf0aa2ed77689` | 2026-11-09T18:20:12.650237+00:00 | Recorded in private supplement; high local confidence | None |
| 2026-11-02 | `BhavCopy_NSE_CM_0_0_0_20261102_F_0000.csv.zip` | `52dbeb12f276c70a5f2d30a719e539b925189360da4709bc37916b970c1ca6e4` | 2026-11-09T18:20:48.270981+00:00 | Recorded in private supplement; high local confidence | None |
| 2026-11-03 | `BhavCopy_NSE_CM_0_0_0_20261103_F_0000.csv.zip` | `ab9edd9caa3f2b51f1e15d783e5a1092eac6029928ee03cbdd17844a25e8141d` | 2026-11-09T18:20:56.214899+00:00 | Recorded in private supplement; high local confidence | None |
| 2026-11-04 | `BhavCopy_NSE_CM_0_0_0_20261104_F_0000.csv.zip` | `786376fb2ac82733f8582989a89ae0270cee84635676405e7461b9afef0d6a96` | 2026-11-09T18:21:18.804215+00:00 | Recorded in private supplement; high local confidence | None |
| 2026-11-05 | `BhavCopy_NSE_CM_0_0_0_20261105_F_0000.csv.zip` | `3b0abdcb89fdc7a7fb57a26b38c01b0df555f9a1b2463b9be5b55eebc22088e2` | 2026-11-09T18:21:26.767388+00:00 | Recorded in private supplement; high local confidence | None |
| 2026-11-06 | `BhavCopy_NSE_CM_0_0_0_20261106_F_0000.csv.zip` | `339e3f07bb00acb27d0200ea5bfa748de71d9b3f779b956d1069f2168c8c9904` | 2026-11-09T18:21:34.652442+00:00 | Recorded in private supplement; high local confidence | None |

The five 26–30 October ZIPs retain their original provenance records in the existing identity evidence manifest. No original ZIP was modified.

## Strict validator result

Command (existing CLI, with the private supplemental manifest):

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend .venv/bin/python -m app.cli validate-nse-references \
  --canonical-database /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/pilot.db \
  --staging-database /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/provisional.db \
  --calendar-json /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/nse-calendar.json \
  --evidence-json /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/historical-identity-evidence.json \
  --reference-dir /Users/mayanksingh/Investment/sample-data \
  --supplemental-manifest /Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/nse-reference-supplement-20261109.json
```

Observed summary (CLI exit **0**):

```json
{"comparison_counts":{"MATCH":95},"database_hashes_unchanged":true,"expected_files":19,"identity_state":"PROVISIONAL","issues":[],"missing_dates":[],"ready_for_reference_review":true,"research_eligible":false,"validated_files":19}
```

Thus all 19 expected files passed the existing ZIP/schema/date/provenance checks and all 95 FYERS–NSE pilot OHLCV comparisons matched exactly. The result is a **reference-review pass**, not a research-eligibility or identity-promotion decision.

## Integrity and boundaries

- All **19/19** original ZIP SHA-256 values still match the Stage D2.2 audit; no originals were changed.
- Canonical `pilot.db` SHA-256 before and after: `c6c6266792b0293841c0163de9585192f5f637101dba9a046597d0e1e9a13850`.
- Provisional `provisional.db` SHA-256 before and after: `e9d815569f75038665d19131debbc950230cb2e639b5ef593be048d56ea12697`.
- The strict CLI independently reported `database_hashes_unchanged: true`. No canonical or provisional rows were written.

## Final reference approval verdict

**APPROVED FOR REFERENCE REVIEW:** the 19 local NSE files now satisfy the existing strict acquisition-provenance contract and reconcile exactly with the 95 provisional FYERS pilot candles. **NOT APPROVED for canonical promotion, real-data ML training, backtesting or portfolio decisions:** FYERS historical ticker–ISIN identity remains provisional; corporate-action adjustment equivalence and point-in-time candle availability remain unverified; data-use rights must remain within the documented personal-use scope. NSE acquisition metadata and OHLCV agreement do not resolve those independent gates. No operator information is currently needed to fill the validator's provenance fields.
