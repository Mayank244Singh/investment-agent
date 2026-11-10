"""One reproducible, synthetic-only daily moving-average research experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT
from app.modules.backtest.costs import CostProfile
from app.modules.backtest.data import Bar, PriceData
from app.modules.backtest.engine import SimulationConfig, SimulationResult, simulate
from app.modules.backtest.report import banner_for_profile, metrics
from app.modules.backtest.run import _canonical
from app.modules.backtest.strategies import buy_and_hold, moving_average_trend
from app.modules.risk.models import RiskLimits

SYMBOL = "SYNTHA"
FAST_SESSIONS = 10
SLOW_SESSIONS = 30
SCENARIO_VERSION = "stage-d1-invented-daily-v1"
PROFILE_PATH = PROJECT_ROOT / "backend/config/backtest-cost-placeholder-2026-11-02.json"


@dataclass(frozen=True)
class SyntheticScenario:
    """Explicit fictional sessions; no NSE calendar or provider observations."""

    data: PriceData
    sessions: tuple[date, ...]

    def __post_init__(self) -> None:
        if self.data.origin != "synthetic" or len(self.sessions) <= SLOW_SESSIONS:
            raise ValueError("D1 requires a synthetic series with a warm-up window")
        if tuple(sorted(set(self.sessions))) != self.sessions:
            raise ValueError("synthetic sessions must be unique and ordered")
        expected = {(SYMBOL, session) for session in self.sessions}
        bars = self.data.ordered_bars()
        if {(bar.symbol, bar.day) for bar in bars} != expected:
            raise ValueError("missing or unexpected synthetic daily candle")
        prior: Bar | None = None
        for bar in bars:
            if prior is not None and bar.previous_close_paise != prior.close_paise:
                raise ValueError("inconsistent synthetic previous close")
            prior = bar


def synthetic_scenario() -> SyntheticScenario:
    """Invented four-regime price path on 100 declared weekday sessions."""
    sessions: list[date] = []
    day = date(2025, 1, 6)
    while len(sessions) < 100:
        if day.weekday() < 5:
            sessions.append(day)
        day += timedelta(days=1)
    closes: list[int] = []
    for index in range(len(sessions)):
        if index < 30:
            close = 10_000 + 20 * index
        elif index < 50:
            close = 10_600 + 10 * (index - 30)
        elif index < 75:
            close = 10_780 - 15 * (index - 50)
        else:
            close = 10_430 + 20 * (index - 75)
        closes.append(close)
    bars = []
    for index, session in enumerate(sessions):
        previous = closes[index - 1] if index else closes[0]
        close = closes[index]
        bars.append(
            Bar(
                SYMBOL, session, previous,
                max(previous, close) + 10,
                min(previous, close) - 10,
                close, previous, 100_000,
            )
        )
    return SyntheticScenario(PriceData(bars, origin="synthetic"), tuple(sessions))


def _configuration(scenario: SyntheticScenario) -> SimulationConfig:
    return SimulationConfig(
        initial_capital_paise=1_000_000,
        universe=(SYMBOL,),
        universe_as_of=scenario.sessions[0],
        risk_limits=RiskLimits(
            Decimal("10"), Decimal("20"), Decimal("1"), Decimal("2"),
            "Asia/Kolkata",
        ),
        stop_below_close_percent=Decimal("5"),
        max_previous_volume_percent=Decimal("5"),
    )


def _result(result: SimulationResult, config: SimulationConfig) -> dict[str, Any]:
    sales = result.realized_sale_pnl_paise
    wins = sum(value > 0 for value in sales)
    win_rate = str(Decimal(wins) * 100 / len(sales)) if sales else None
    return {
        "metrics": metrics(result, config),
        "closed_sales": len(sales),
        "winning_sales": wins,
        "win_rate_percent": win_rate,
        "equity_curve": result.equity_curve,
        "fills": result.fills,
        "rejections": result.rejections,
        "warnings": result.warnings,
    }


def run_experiment(scenario: SyntheticScenario | None = None) -> dict[str, Any]:
    """Run only the supplied synthetic series, never a DB or provider reader."""
    scenario = scenario or synthetic_scenario()
    config = _configuration(scenario)
    profile = replace(
        CostProfile.from_json(PROFILE_PATH),
        slippage_bps=Decimal("10"),
    )
    first_eval = scenario.sessions[SLOW_SESSIONS]
    benchmark_decision = scenario.sessions[SLOW_SESSIONS - 1]
    trend = simulate(
        scenario.data, profile, config,
        moving_average_trend(config, symbol=SYMBOL), trading_start=first_eval,
    )
    hold = simulate(
        scenario.data, profile, config,
        buy_and_hold(config, decision_start=benchmark_decision),
        trading_start=first_eval,
    )
    cash = simulate(
        scenario.data, profile, config, lambda view, summary: (),
        trading_start=first_eval,
    )
    config_bytes = _canonical({
        "simulation": config, "profile": profile,
        "fast_sessions": FAST_SESSIONS, "slow_sessions": SLOW_SESSIONS,
        "evaluation_start": first_eval,
    })
    bars = scenario.data.ordered_bars()
    record = {
        "schema_version": "stage-d1-report-v1",
        "scenario_version": SCENARIO_VERSION,
        "synthetic": True,
        "source": "Invented daily OHLCV; no external market-data or broker input",
        "banner": banner_for_profile(profile),
        "config_hash_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "data_hash_sha256": hashlib.sha256(_canonical(bars)).hexdigest(),
        "configuration": {
            "simulation": config, "fast_sessions": FAST_SESSIONS,
            "slow_sessions": SLOW_SESSIONS,
            "warmup_sessions": SLOW_SESSIONS,
            "evaluation_start": first_eval,
            "evaluation_end": scenario.sessions[-1],
        },
        "cost_profile": profile,
        "data_coverage": {
            "symbol": SYMBOL, "sessions": len(bars),
            "first_date": scenario.sessions[0],
            "last_date": scenario.sessions[-1],
        },
        "results": {
            "ma_trend_10_30": _result(trend, config),
            "buy_and_hold": _result(hold, config),
            "cash_only": _result(cash, config),
        },
        "limitations": [
            "Synthetic outcomes do not demonstrate a profitable trading edge.",
            "Weekday sessions are invented; no NSE holiday or halt claim.",
            "Costs and 10 bps adverse slippage are illustrative, unverified rates.",
            "Daily OHLC has no intraday execution sequence or liquidity evidence.",
            "No annualised metrics are reported for fewer than 252 sessions.",
        ],
    }
    return json.loads(_canonical(record))


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Stage D1 — synthetic moving-average research result", "",
        "**SYNTHETIC ONLY — NOT MARKET DATA OR EVIDENCE OF AN EDGE.**", "",
        report["banner"], "",
        "The strategy is long when the 10-session completed-close average exceeds "
        "the 30-session average, otherwise cash. Signals form after the close; "
        "fills occur no earlier than the next session's open. Both invested "
        "arms use the same risk gate, cost profile and 10 bps adverse slippage.",
        "", "| Arm | Net return | Max drawdown | Trades | Win rate | "
        "Turnover | Exposure |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for key, label in (
        ("ma_trend_10_30", "MA 10/30"),
        ("buy_and_hold", "Buy and hold"),
        ("cash_only", "Cash only"),
    ):
        result = report["results"][key]
        values = result["metrics"]
        rate = result["win_rate_percent"]
        rate_text = f"{Decimal(rate):.2f}%" if rate is not None else "undefined"
        lines.append(
            f"| {label} | {Decimal(values['net_return_percent']):.2f}% | "
            f"{Decimal(values['max_drawdown_percent']):.2f}% | "
            f"{values['trade_count']} | {rate_text}"
            f" | {Decimal(values['turnover_percent']):.2f}% | "
            f"{Decimal(values['mean_exposure_percent']):.2f}% |"
        )
    trend = report["results"]["ma_trend_10_30"]["metrics"]
    lines.extend([
        "", f"Evaluation: {trend['trading_days']} invented sessions, "
        f"{trend['first_date']} to {trend['last_date']} "
        f"(after {SLOW_SESSIONS} warm-up sessions).",
        f"Initial capital: {trend['initial_capital_paise']} paise; "
        f"final trend equity: {trend['final_net_equity_paise']} paise.",
        f"Trend fees: {trend['total_costs_paise']} paise; "
        f"slippage impact: {trend['slippage_impact_paise']} paise; "
        f"cost drag: {trend['cost_drag_paise']} paise.",
        "", f"Configuration SHA-256: `{report['config_hash_sha256']}`",
        f"Synthetic bars SHA-256: `{report['data_hash_sha256']}`", "",
        "Equity curves, individual fills and all integer-paise metrics are in "
        "the companion JSON report. The source cost profile is dated and "
        "unverified; its rates are illustrative, not broker quotes.", "",
        "## Limits", "",
    ])
    lines.extend(f"- {item}" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def write_reports(directory: Path) -> tuple[Path, Path]:
    """Write deterministic artifacts in a caller-selected fresh directory."""
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / "stage-d1-synthetic-report.json"
    markdown_path = directory / "stage-d1-synthetic-report.md"
    if json_path.exists() or markdown_path.exists():
        raise FileExistsError("choose a fresh report directory")
    report = run_experiment()
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    markdown_path.write_text(render_markdown(report))
    return json_path, markdown_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Run synthetic-only Stage D1 research")
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    paths = write_reports(arguments.output_dir)
    for path in paths:
        print(path)


if __name__ == "__main__":
    main()
