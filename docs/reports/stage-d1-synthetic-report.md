# Stage D1 — synthetic moving-average research result

**SYNTHETIC ONLY — NOT MARKET DATA OR EVIDENCE OF AN EDGE.**

Past results are not predictions or recommendations. Survivorship bias applies if the universe uses today's constituents. Prices are unadjusted. Short histories cannot support conclusions. No index benchmark: index data is not stored. Cost profile unverified; synthetic results only. Charges and returns are illustrative.

The strategy is long when the 10-session completed-close average exceeds the 30-session average, otherwise cash. Signals form after the close; fills occur no earlier than the next session's open. Both invested arms use the same risk gate, cost profile and 10 bps adverse slippage.

| Arm | Net return | Max drawdown | Trades | Win rate | Turnover | Exposure |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| MA 10/30 | 0.18% | 0.19% | 3 | 0.00% | 28.68% | 6.06% |
| Buy and hold | 0.26% | 0.33% | 1 | undefined | 9.53% | 9.58% |
| Cash only | 0.00% | 0.00% | 0 | undefined | 0.00% | 0.00% |

Evaluation: 70 invented sessions, 2025-02-17 to 2025-05-23 (after 30 warm-up sessions).
Initial capital: 1000000 paise; final trend equity: 1001813 paise.
Trend fees: 680 paise; slippage impact: 297 paise; cost drag: 977 paise.

Configuration SHA-256: `e8333e9e3d6b64def06bd75799e4a79a1762ff3c6095032c4c3d72c95bc80ce8`
Synthetic bars SHA-256: `0f19369d1f2eec353f488b1e70ea650345be0ff310c552800ddd2c862bf36ea8`

Equity curves, individual fills and all integer-paise metrics are in the companion JSON report. The source cost profile is dated and unverified; its rates are illustrative, not broker quotes.

## Limits

- Synthetic outcomes do not demonstrate a profitable trading edge.
- Weekday sessions are invented; no NSE holiday or halt claim.
- Costs and 10 bps adverse slippage are illustrative, unverified rates.
- Daily OHLC has no intraday execution sequence or liquidity evidence.
- No annualised metrics are reported for fewer than 252 sessions.
