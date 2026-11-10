"""Stage D1 invented-candle research, with no provider or production data."""

import json
import socket
from dataclasses import replace
from decimal import Decimal

import pytest

from app.modules.backtest.data import PriceData
from app.modules.backtest.strategies import moving_average_trend
from app.modules.backtest.trend_research import (
    SLOW_SESSIONS,
    SYMBOL,
    SyntheticScenario,
    _configuration,
    render_markdown,
    run_experiment,
    synthetic_scenario,
    write_reports,
)
from app.modules.portfolio.ledger_service import PortfolioSummary


def _empty_summary() -> PortfolioSummary:
    return PortfolioSummary(1_000_000, 1_000_000, 0, 0, 0, 1_000_000, ())


def test_warmup_uses_completed_closes_and_never_sees_future_bar() -> None:
    scenario = synthetic_scenario()
    strategy = moving_average_trend(_configuration(scenario), symbol=SYMBOL)
    assert strategy(scenario.data.view(scenario.sessions[28]), _empty_summary()) == ()
    decision_day = scenario.sessions[SLOW_SESSIONS - 1]
    original = strategy(scenario.data.view(decision_day), _empty_summary())
    assert len(original) == 1 and original[0].side == "buy"

    bars = list(scenario.data.ordered_bars())
    future = bars[-1]
    bars[-1] = replace(future, high_paise=90_000, close_paise=90_000)
    changed = PriceData(bars, origin="synthetic")
    assert strategy(changed.view(decision_day), _empty_summary()) == original
    with pytest.raises(ValueError, match="future bars"):
        scenario.data.view(decision_day).get(SYMBOL, scenario.sessions[-1])


def test_first_fill_uses_next_session_open_and_includes_slippage() -> None:
    scenario = synthetic_scenario()
    bars = list(scenario.data.ordered_bars())
    index = SLOW_SESSIONS
    bars[index] = replace(
        bars[index], open_paise=10_620, high_paise=10_630,
    )
    changed = SyntheticScenario(PriceData(bars, origin="synthetic"), scenario.sessions)
    first = run_experiment(changed)["results"]["ma_trend_10_30"]["fills"][0]
    assert first["day"] == scenario.sessions[index].isoformat()
    assert first["price_paise"] == 10_631  # next open + 10 bps adverse slippage
    assert first["reason"] == "strategy"


def test_missing_duplicate_invalid_and_non_synthetic_bars_fail_closed() -> None:
    scenario = synthetic_scenario()
    bars = list(scenario.data.ordered_bars())
    with pytest.raises(ValueError, match="missing or unexpected"):
        SyntheticScenario(PriceData(bars[1:], origin="synthetic"), scenario.sessions)
    with pytest.raises(ValueError, match="duplicate bar"):
        PriceData([*bars, bars[-1]], origin="synthetic")
    with pytest.raises(ValueError, match="outside daily range"):
        replace(bars[0], low_paise=bars[0].open_paise + 1)
    with pytest.raises(ValueError, match="synthetic series"):
        SyntheticScenario(PriceData(bars, origin="stored"), scenario.sessions)
    with pytest.raises(ValueError, match="inconsistent synthetic previous close"):
        SyntheticScenario(
            PriceData(
                [bars[0], replace(bars[1], previous_close_paise=1), *bars[2:]],
                origin="synthetic",
            ),
            scenario.sessions,
        )


def test_flat_synthetic_series_has_zero_trend_trades_and_undefined_win_rate() -> None:
    scenario = synthetic_scenario()
    bars = [
        replace(
            bar, open_paise=10_000, high_paise=10_010, low_paise=9_990,
            close_paise=10_000, previous_close_paise=10_000,
        )
        for bar in scenario.data.ordered_bars()[:40]
    ]
    flat = SyntheticScenario(
        PriceData(bars, origin="synthetic"), scenario.sessions[:40]
    )
    result = run_experiment(flat)["results"]["ma_trend_10_30"]
    assert result["metrics"]["trade_count"] == 0
    assert result["closed_sales"] == 0
    assert result["win_rate_percent"] is None
    assert result["metrics"]["final_net_equity_paise"] == 1_000_000


def test_costs_fifo_win_rate_and_benchmark_reconciliation() -> None:
    report = run_experiment()
    results = report["results"]
    trend = results["ma_trend_10_30"]
    values = trend["metrics"]
    assert sum(fill["charges"]["brokerage_paise"] for fill in trend["fills"]) > 0
    assert sum(sum(fill["charges"].values()) for fill in trend["fills"]) == (
        values["total_costs_paise"]
    )
    assert values["slippage_impact_paise"] > 0
    assert values["cost_drag_paise"] == (
        values["total_costs_paise"] + values["slippage_impact_paise"]
    )
    assert values["final_gross_equity_paise"] == (
        values["final_net_equity_paise"] + values["cost_drag_paise"]
    )
    assert trend["closed_sales"] == 1
    assert trend["winning_sales"] == 0
    assert Decimal(trend["win_rate_percent"]) == 0
    assert values["trading_days"] == len(trend["equity_curve"]) == 70
    assert values["final_net_equity_paise"] == 1_001_813
    assert values["total_costs_paise"] == 680
    assert values["cost_drag_paise"] == 977
    assert results["buy_and_hold"]["metrics"]["final_net_equity_paise"] == (
        1_002_648
    )
    assert results["cash_only"]["metrics"]["trade_count"] == 0
    assert results["cash_only"]["metrics"]["net_return_percent"] == "0"
    assert results["buy_and_hold"]["fills"][0]["day"] == (
        trend["fills"][0]["day"]
    )
    assert report["cost_profile"]["verified"] is False
    assert report["cost_profile"]["slippage_bps"] == "10"


def test_experiment_does_not_open_a_network_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_socket(*_: object, **__: object) -> None:
        raise AssertionError("synthetic research opened a network socket")

    monkeypatch.setattr(socket, "socket", forbidden_socket)
    assert run_experiment()["synthetic"] is True


def test_reproducible_report_and_companion_markdown(tmp_path) -> None:
    first = run_experiment()
    second = run_experiment()
    assert first == second
    assert first["synthetic"] is True
    assert len(first["config_hash_sha256"]) == 64
    assert len(first["data_hash_sha256"]) == 64
    assert "provisional" not in first["source"].lower()
    markdown = render_markdown(first)
    assert "SYNTHETIC ONLY" in markdown
    assert "Buy and hold" in markdown and "Cash only" in markdown
    json_path, markdown_path = write_reports(tmp_path / "report")
    assert json.loads(json_path.read_text()) == first
    assert markdown_path.read_text() == markdown
    with pytest.raises(FileExistsError, match="fresh report directory"):
        write_reports(tmp_path / "report")
