# Stage D2.6 — NSE Capital Market daily-session calendar

**Review date:** 11 November 2026 (Asia/Kolkata). **Scope:** NSE cash equities,
10 November 2021–9 November 2026 inclusive. This is daily-session evidence for
the isolated FYERS collector, not an intraday execution calendar.

## Artifact and method

The explicit 1,826-date JSON is stored privately at
`/Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1/nse-calendar-20211110-20261109.json`
(mode `0600`; SHA-256
`4e07a63f79d1fd8005b0b0c7394be7458e47ede0e539b6614baf4938ead2ca94`).
The original 19-session `nse-calendar.json` was retained unchanged. All 19
sessions and its 20 October 2026 holiday match the new calendar exactly.

[NSE's equity trading-hours page](https://www.nseindia.com/resources/exchange-communication-holidays)
states the ordinary Monday–Friday rule, except exchange-declared holidays,
and the normal 09:15–15:30 IST continuous session. Each ordinary date in the
JSON was classified from that **published rule plus the year-specific NSE
Capital Market notices and amendments below**; it is not an assumption that
all weekdays traded. Weekends are explicitly non-trading except separately
notified live sessions. Exchange mock sessions were not counted. The
`holidays` array represents *all non-trading dates*, including ordinary
weekends, because the existing schema has only `sessions` and `holidays`.
Muhurat dates with live trades are `special` sessions, even when the annual
notice calls them holidays.

The annual notices and follow-up notices were retrieved on 10–11 November
2026 and reviewed on 11 November 2026:

| Year in scope | Retrieval | Annual NSE Capital Market notice (publication date) | Amendments and special live-session evidence |
| --- | --- | --- | --- |
| 2021, from 10 Nov | 10–11 Nov 2026 | [CMTR46623](https://archives.nseindia.com/content/circulars/CMTR46623.pdf), 11 Dec 2020 | 19 Nov holiday; no separately evidenced live weekend in the scoped weeks. |
| 2022 | 10–11 Nov 2026 | [CMTR50560](https://archives.nseindia.com/content/circulars/CMTR50560.pdf), 10 Dec 2021 | [CMTR54023](https://nsearchives.nseindia.com/content/circulars/CMTR54023.pdf), 11 Oct 2022: 24 Oct Muhurat, 18:15–19:15. |
| 2023 | 10–11 Nov 2026 | [CMTR54757](https://archives.nseindia.com/content/circulars/CMTR54757.pdf), 8 Dec 2022 | Bakri Id moved from 28 to 29 June by NSE/CMTR/57285 (27 Jun 2023), corroborated by [NSE Indices](https://niftyindices.com/Press_Release/ind_prs27062023.pdf) and [NSE Clearing](https://nsearchives.nseindia.com/content/circulars/CMPT57296.pdf); [CMTR59124](https://nsearchives.nseindia.com/content/circulars/CMTR59124.pdf), 27 Oct 2023: Sunday 12 Nov Muhurat, 18:15–19:15. |
| 2024 | 10–11 Nov 2026 | [CMTR59722](https://nsearchives.nseindia.com/content/circulars/CMTR59722.pdf), 12 Dec 2023 | 22 Jan closure and 20 Jan live Saturday documented by [NSE Clearing CMPT60343](https://nsearchives.nseindia.com/content/circulars/CMPT60343.pdf), 19 Jan 2024, superseding the earlier split-session plan in [MSD59999](https://nsearchives.nseindia.com/content/circulars/MSD59999.pdf); [MSD60677](https://nsearchives.nseindia.com/content/circulars/MSD60677.pdf), 14 Feb: 2 Mar live Saturday; [CMTR61518](https://nsearchives.nseindia.com/content/circulars/CMTR61518.pdf), 8 Apr: 20 May election holiday; [MSD61893](https://nsearchives.nseindia.com/content/circulars/MSD61893.pdf), 7 May: 18 May live Saturday; [CMTR64628](https://nsearchives.nseindia.com/content/circulars/CMTR64628.pdf), 19 Oct: 1 Nov Muhurat; [CMTR64960](https://nsearchives.nseindia.com/content/circulars/CMTR64960.pdf), 8 Nov: 20 Nov election holiday. |
| 2025 | 10–11 Nov 2026 | [CMTR65587](https://nsearchives.nseindia.com/content/circulars/CMTR65587.pdf), 13 Dec 2024 | [CMTR65729](https://nsearchives.nseindia.com/content/circulars/CMTR65729.pdf), 23 Dec 2024: Saturday 1 Feb Union Budget session; [CMTR70319](https://nsearchives.nseindia.com/content/circulars/CMTR70319.pdf), 22 Sep 2025: 21 Oct Muhurat, 13:45–14:45. |
| 2026, through 9 Nov | 10–11 Nov 2026 | [CMTR71775](https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf), 12 Dec 2025 | [CMTR72260](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf), 12 Jan: 15 Jan election holiday; [CMTR72349](https://nsearchives.nseindia.com/content/circulars/CMTR72349.pdf), 16 Jan: Sunday 1 Feb Union Budget session; [CMTR76941](https://nsearchives.nseindia.com/content/circulars/CMTR76941.pdf), 19 Oct: Sunday 8 Nov Muhurat, 14:20 normal-market opening, non-CAS close 15:30 and CAS completion 15:35. |

The primary CMTR57285 and CMTR60338 PDFs were not retrievable during this
review; the linked NSE group notices explicitly cite the former and the
revised January 2024 trading/settlement schedule. The updated 20 January
session uses the normal 09:15–15:30 boundary, not MSD59999's subsequently
superseded two-session plan.

## Coverage and cross-checks

| Year | Dates | Trading sessions | Non-trading dates | Special sessions |
| --- | ---: | ---: | ---: | ---: |
| 2021, from 10 Nov | 52 | 37 | 15 | 0 |
| 2022 | 365 | 248 | 117 | 1 |
| 2023 | 365 | 246 | 119 | 1 |
| 2024 | 366 | 249 | 117 | 4 |
| 2025 | 365 | 249 | 116 | 2 |
| 2026, through 9 Nov | 313 | 212 | 101 | 2 |
| **Total** | **1,826** | **1,241** | **585** | **10** |

Of the 585 non-trading dates, 70 are weekday exchange holidays and 515 are
ordinary weekends. Seven of the ten special sessions occur on weekends.
There are no unclassified dates, duplicate session dates or session/holiday
overlaps. The 19 local NSE UDiFF ZIP dates are all classified as sessions;
all 19 recorded reference SHA-256 hashes match their ZIP bytes. Existing
reference ZIPs and both SQLite databases were read only.

The three 2024 DR Saturdays need special care: 20 January became a full
normal-hours session after the 22 January holiday was announced; 2 March
and 18 May had a break between 10:00 and 11:30. A single open/close pair
cannot encode that intraday gap. Likewise, 8 November 2026 has different
continuous-market and closing-auction endpoints by security. These records
are **daily collector classifications only** and must not be reused as a
minute-bar calendar without a richer, separately reviewed session model.

No full-day exceptional closure beyond the cited holiday amendments was
established in the official notices reviewed. No `halts` were invented.
An undiscovered exceptional notice or intraday disruption remains a
residual risk; incoming candles must still be checked against the calendar
and independent NSE reports. Only the 19 supplied ZIP dates currently have
per-day NSE reference confirmation.

## Offline verification and command correction

The existing parser accepted the 174,080-byte calendar. A focused regression
test now requires **every date**, including weekends, to be classified during
the five-year collector preflight; this tightens the gate. Local calendar
coverage passed and all 19 ZIP dates matched. The first full preflight
stopped with exit 2:

```text
FYERS collection BLOCKED: FYERS master and identity evidence differ
```

The private FYERS master subset has SHA-256
`d4dbc9205c900dde34d4b0018058aacaac5adceb062d322dadf18f53ef6a9128`,
but the historical-identity manifest records
`fbedcb0de69e4181185217fe9943a30c6f283a09e9e29a9138d7c8eb5fec35d6`.
The latter is the exact SHA-256 of the existing private **full**
`NSE_CM_sym_master.json`. Passing the full master as `--master-json` resolved
the input mismatch without changing either file or the identity manifest.
The repeated offline preflight exited 0:

```json
{"credentials_read": false, "database_written": false, "identity_state": "PROVISIONAL", "instruments": ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"], "nse_reference_rows": 95, "preflight": "PASS", "request_budget": 25, "research_eligible": false, "windows": 25}
```

This approves the **offline collector prerequisites only**. Historical FYERS
identity, corporate-action equivalence and candle availability remain
unverified; all collected observations must remain provisional. Zero FYERS
requests were made in this calendar stage.

## Exact future command

Before any separately approved acquisition, rerun the following **offline**
command and require `preflight: PASS` again:

```zsh
cd /Users/mayanksingh/.codex/worktrees/phase-7b-integration/investment-agent
pilot=/Users/mayanksingh/InvestmentAgentData/pilots/fyers-b1
args=(
  --universe-file docs/examples/fyers-five-stock-universe.json
  --from-date 2021-11-10 --to-date 2026-11-09
  --database "$pilot/pilot.db" --staging-database "$pilot/provisional.db"
  --raw-dir "$pilot/raw" --master-json "$pilot/NSE_CM_sym_master.json"
  --evidence-json "$pilot/historical-identity-evidence.json"
  --calendar-json "$pilot/nse-calendar-20211110-20261109.json"
  --nse-zip-dir /Users/mayanksingh/Investment/sample-data
  --supplemental-manifest "$pilot/nse-reference-supplement-20261109.json"
  --token-file /Users/mayanksingh/fyers-auth/.access_token
  --max-requests 25
)
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend .venv312/bin/python -m app.cli \
  collect-fyers-history "${args[@]}" --preflight-only
```

Only after a separate live-acquisition approval and a passing preflight,
the exact acquisition invocation is:

```zsh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend .venv312/bin/python -m app.cli \
  collect-fyers-history "${args[@]}" --allow-live --confirm-persist
```

No live acquisition, credential read, canonical promotion or model training
was performed in Stage D2.6.
