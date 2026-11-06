"""Synthetic baselines, benchmarks, metrics, windows, CLI, and run records."""

import json
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.cli import main
from app.modules.backtest.costs import CostProfile
from app.modules.backtest.data import Bar, PriceData
from app.modules.backtest.engine import EquityPoint, SimulationConfig, simulate
from app.modules.backtest.report import (
    BANNER,
    UNVERIFIED_COST_WARNING,
    evaluate,
    metrics,
)
from app.modules.backtest.run import RunSettings, evaluate_windows, save_run
from app.modules.backtest.strategies import buy_and_hold, momentum_ranking
from app.modules.portfolio.ledger_service import PortfolioSummary
from app.modules.risk.models import RiskLimits

DAY = date(2026, 1, 1)
SYMBOLS = tuple(f"TEST{letter}" for letter in "ABCDEFGHIJ")


def zero_profile() -> CostProfile:
    return CostProfile(
        "SYNTHETIC_ZERO_COST",
        DAY,
        "Invented zero charges for hand checks",
        False,
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        Decimal(0),
        0,
        Decimal(0),
    )


def configuration(*, universe: tuple[str, ...] = SYMBOLS) -> SimulationConfig:
    return SimulationConfig(
        100_000,
        universe,
        DAY,
        RiskLimits(Decimal(10), Decimal(20), Decimal(1), Decimal(2), "Asia/Kolkata"),
        max_previous_volume_percent=Decimal(100),
        allow_zero_costs=True,
    )


def bars(days: int = 2, *, rising: bool = True) -> PriceData:
    rows = []
    for index in range(days):
        for symbol in SYMBOLS:
            close = 1100 if rising and index else 1000
            previous = 1100 if rising and index > 1 else 1000
            rows.append(
                Bar(
                    symbol,
                    DAY + timedelta(days=index),
                    1000 if index == 1 else close,
                    max(close, 1000),
                    min(close, 1000),
                    close,
                    previous,
                    1_000_000,
                )
            )
    return PriceData(rows, origin="synthetic")


def test_equal_weight_benchmark_matches_hand_calculation() -> None:
    cfg = configuration()
    report = evaluate(
        bars(), zero_profile(), cfg, buy_and_hold(cfg), strategy_family="buy_and_hold"
    )
    assert BANNER in report.banner
    assert UNVERIFIED_COST_WARNING in report.banner
    assert UNVERIFIED_COST_WARNING in report.warnings
    assert report.metrics.trading_days == 2
    assert (report.metrics.first_date, report.metrics.last_date) == (
        DAY,
        DAY + timedelta(days=1),
    )
    assert report.metrics.final_net_equity_paise == 110_000
    assert report.metrics.net_return_percent == Decimal(10)
    constrained, unconstrained, cash = report.benchmarks
    assert "constrained" in constrained.label
    assert "unconstrained" in unconstrained.label
    assert "Cash" in cash.label
    assert constrained.metrics.final_net_equity_paise == 110_000
    assert unconstrained.metrics.final_net_equity_paise == 110_000
    assert cash.metrics.final_net_equity_paise == 100_000
    assert "No index benchmark" in report.banner


def test_constrained_benchmark_uses_validator_and_needs_ten_slots() -> None:
    cfg = configuration()
    cfg = replace(
        cfg,
        risk_limits=RiskLimits(
            Decimal(1), Decimal(20), Decimal(1), Decimal(2), "Asia/Kolkata"
        ),
    )
    report = evaluate(
        bars(), zero_profile(), cfg, buy_and_hold(cfg), strategy_family="buy_and_hold"
    )
    constrained, unconstrained, _ = report.benchmarks
    assert constrained.metrics.final_net_equity_paise == 101_000
    assert unconstrained.metrics.final_net_equity_paise == 110_000
    small = configuration(universe=("TESTA", "TESTB"))
    with pytest.raises(ValueError, match="10 instruments or tranches"):
        evaluate(
            bars(),
            zero_profile(),
            small,
            buy_and_hold(small),
            strategy_family="buy_and_hold",
        )


def test_determinism_same_inputs_same_results() -> None:
    cfg = configuration()
    first = evaluate(
        bars(), zero_profile(), cfg, buy_and_hold(cfg), strategy_family="buy_and_hold"
    )
    second = evaluate(
        bars(), zero_profile(), cfg, buy_and_hold(cfg), strategy_family="buy_and_hold"
    )
    assert first == second


def test_momentum_ranks_trailing_closes_and_ties_by_symbol() -> None:
    cfg = configuration(universe=("TESTA", "TESTB"))
    dataset = PriceData(
        (
            Bar("TESTA", DAY, 1000, 1000, 1000, 1000, 1000, 1000),
            Bar("TESTB", DAY, 1000, 1000, 1000, 1000, 1000, 1000),
            Bar("TESTA", DAY + timedelta(days=1), 1100, 1100, 1100, 1100, 1000, 1000),
            Bar("TESTB", DAY + timedelta(days=1), 900, 900, 900, 900, 1000, 1000),
        ),
        origin="synthetic",
    )
    summary = PortfolioSummary(100_000, 100_000, 0, 0, 0, 100_000, ())
    strategy = momentum_ranking(cfg, lookback_days=1, top_k=1, rebalance_days=2)
    assert strategy(dataset.view(DAY), summary) == ()
    intents = strategy(dataset.view(DAY + timedelta(days=1)), summary)
    assert len(intents) == 1
    assert intents[0].symbol == "TESTA"
    assert intents[0].stop_loss_paise == 1045


def test_windows_have_separate_boundaries_and_portfolios() -> None:
    cfg = configuration()
    settings = RunSettings(cfg, "buy_and_hold", window_size_days=2)
    windows = evaluate_windows(bars(4, rising=False), zero_profile(), settings)
    assert [(item.start, item.end) for item in windows] == [
        (DAY, DAY + timedelta(days=1)),
        (DAY + timedelta(days=2), DAY + timedelta(days=3)),
    ]
    assert [item.report.metrics.trading_days for item in windows] == [2, 2]
    assert all(
        BANNER in item.report.banner and UNVERIFIED_COST_WARNING in item.report.banner
        for item in windows
    )
    assert [item.report.metrics.initial_capital_paise for item in windows] == [
        100_000,
        100_000,
    ]
    for window in windows:
        assert all(
            window.start <= fill.day <= window.end
            for fill in window.report.simulation.fills
        )


def test_trial_counter_increments_and_record_has_provenance(tmp_path: Path) -> None:
    cfg = configuration()
    settings = RunSettings(cfg, "buy_and_hold")
    first = save_run(bars(), zero_profile(), settings, tmp_path / "runs", tmp_path)
    second = save_run(bars(), zero_profile(), settings, tmp_path / "runs", tmp_path)
    assert (first.trial_number, second.trial_number) == (1, 2)
    record = json.loads(second.record_path.read_text())
    assert record["trial_number"] == 2
    assert record["config_hash_sha256"] and record["data_hash_sha256"]
    assert record["universe"] == list(SYMBOLS)
    assert record["cost_profile"]["verified"] is False
    assert record["data_coverage"]["TESTA"]["rows"] == 2
    assert record["windows"][0]["report"]["metrics"]["trading_days"] == 2
    assert BANNER in record["banner"]
    assert UNVERIFIED_COST_WARNING in record["banner"]
    other = save_run(
        bars(),
        zero_profile(),
        RunSettings(cfg, "momentum", momentum_lookback_days=1),
        tmp_path / "runs",
        tmp_path,
    )
    assert other.trial_number == 1


def test_insufficient_history_refuses_annualised_metrics() -> None:
    cfg = configuration()
    result = simulate(bars(), zero_profile(), cfg, lambda view, summary: ())
    short = metrics(result, cfg)
    assert short.annualized_return_percent is None
    assert short.annualized_volatility_percent is None
    start = result.equity_curve[0].day
    long_curve = tuple(
        EquityPoint(start + timedelta(days=index), 100_000, 0, ())
        for index in range(252)
    )
    long = metrics(replace(result, equity_curve=long_curve), cfg)
    assert long.annualized_return_percent == 0
    assert long.annualized_volatility_percent == 0


def test_cost_drag_and_rejection_counts_are_reported() -> None:
    cfg = replace(
        configuration(),
        risk_limits=RiskLimits(
            Decimal(20), Decimal(20), Decimal(1), Decimal(2), "Asia/Kolkata"
        ),
    )
    charged = replace(
        zero_profile(), brokerage_bps=Decimal(10), slippage_bps=Decimal(10)
    )
    result = simulate(bars(), charged, cfg, buy_and_hold(cfg))
    values = metrics(result, cfg)
    assert values.total_costs_paise > 0
    assert values.slippage_impact_paise > 0
    assert values.cost_drag_paise == (
        values.total_costs_paise + values.slippage_impact_paise
    )
    assert values.final_gross_equity_paise == (
        values.final_net_equity_paise + values.cost_drag_paise
    )
    assert values.rejected_trade_count == len(result.rejections)


def test_placeholder_costs_create_gross_net_gap_after_a_fill() -> None:
    """Synthetic placeholder charges must be visible in reported returns."""
    path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / ("backtest-cost-placeholder-2026-11-02.json")
    )
    placeholder = CostProfile.from_json(path)
    assert not placeholder.verified
    cfg = replace(
        configuration(),
        risk_limits=RiskLimits(
            Decimal(20), Decimal(20), Decimal(1), Decimal(2), "Asia/Kolkata"
        ),
    )
    result = simulate(bars(), placeholder, cfg, buy_and_hold(cfg))
    values = metrics(result, cfg)
    assert result.fills
    assert values.gross_return_percent > values.net_return_percent
    assert values.total_costs_paise > 0
    assert values.cost_drag_paise >= values.total_costs_paise


def test_zero_transaction_costs_refused_without_explicit_override() -> None:
    cfg = replace(configuration(), allow_zero_costs=False)
    with pytest.raises(ValueError, match="allow_zero_costs=true"):
        simulate(bars(), zero_profile(), cfg, buy_and_hold(cfg))
    gst_only = replace(zero_profile(), gst_bps=Decimal("1800"))
    with pytest.raises(ValueError, match="allow_zero_costs=true"):
        simulate(bars(), gst_only, cfg, buy_and_hold(cfg))
    slippage_only = replace(zero_profile(), slippage_bps=Decimal("10"))
    with pytest.raises(ValueError, match="allow_zero_costs=true"):
        simulate(bars(), slippage_only, cfg, buy_and_hold(cfg))


def test_zero_transaction_cost_override_warns() -> None:
    cfg = configuration()
    result = simulate(bars(), zero_profile(), cfg, buy_and_hold(cfg))
    assert result.fills
    assert any(
        "Zero transaction costs explicitly allowed" in warning
        for warning in result.warnings
    )


def test_stored_prices_require_positive_slippage_even_with_costs() -> None:
    profile_path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / ("backtest-cost-placeholder-2026-11-02.json")
    )
    charged = replace(CostProfile.from_json(profile_path), verified=True)
    stored_origin = PriceData(bars().ordered_bars(), origin="stored")
    with pytest.raises(ValueError, match="positive slippage"):
        simulate(stored_origin, charged, configuration(), buy_and_hold(configuration()))


def test_stored_prices_with_unknown_source_previous_close_refuse_research_run() -> None:
    profile_path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "backtest-cost-placeholder-2026-11-02.json"
    )
    charged = replace(
        CostProfile.from_json(profile_path), verified=True, slippage_bps=Decimal(1)
    )
    rows = list(bars().ordered_bars())
    rows[0] = replace(rows[0], previous_close_paise=None)
    synthetic = PriceData(rows, origin="synthetic")
    simulate(synthetic, charged, configuration(), buy_and_hold(configuration()))
    stored = PriceData(rows, origin="stored")
    with pytest.raises(ValueError, match="source previous close"):
        simulate(stored, charged, configuration(), buy_and_hold(configuration()))


def test_cli_banner_and_no_env_dependency(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = {
        "initial_capital_paise": 100_000,
        "universe": list(SYMBOLS),
        "universe_as_of": DAY.isoformat(),
        "strategy_family": "buy_and_hold",
        "max_previous_volume_percent": "100",
        "risk_limits": {
            "max_order_percent": "10",
            "max_stock_allocation_percent": "20",
            "max_loss_per_trade_percent": "1",
            "max_daily_loss_percent": "2",
            "daily_loss_timezone": "Asia/Kolkata",
        },
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(cfg))
    bars_path = tmp_path / "bars.json"
    bars_path.write_text(
        json.dumps(
            [
                {
                    "symbol": row.symbol,
                    "day": row.day.isoformat(),
                    "open_paise": row.open_paise,
                    "high_paise": row.high_paise,
                    "low_paise": row.low_paise,
                    "close_paise": row.close_paise,
                    "previous_close_paise": row.previous_close_paise,
                    "volume": row.volume,
                }
                for row in bars().ordered_bars()
            ]
        )
    )
    profile_path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / ("backtest-cost-placeholder-2026-11-02.json")
    )
    monkeypatch.setattr(
        "app.cli.get_settings",
        lambda: (_ for _ in ()).throw(AssertionError("backtest must not read .env")),
    )
    assert (
        main(
            [
                "backtest",
                "--config",
                str(config_path),
                "--cost-profile",
                str(profile_path),
                "--synthetic-bars",
                str(bars_path),
                "--runs-directory",
                str(tmp_path / "runs"),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert BANNER in output
    assert UNVERIFIED_COST_WARNING in output
    assert "trial #1" in output
    assert "2 trading days" in output
    assert "No index benchmark" in output
    assert (
        main(
            [
                "backtest",
                "--config",
                str(config_path),
                "--cost-profile",
                str(profile_path),
                "--database",
                str(tmp_path / "absent.db"),
                "--runs-directory",
                str(tmp_path / "runs"),
            ]
        )
        == 1
    )
    capsys.readouterr()
    zero_profile_data = json.loads(profile_path.read_text())
    for field in (
        "brokerage_bps",
        "stt_buy_bps",
        "stt_sell_bps",
        "exchange_bps",
        "regulator_bps",
        "stamp_buy_bps",
        "gst_bps",
    ):
        zero_profile_data[field] = "0"
    zero_profile_data["depository_sell_paise"] = 0
    zero_path = tmp_path / "zero-cost-profile.json"
    zero_path.write_text(json.dumps(zero_profile_data))
    zero_args = [
        "backtest",
        "--config",
        str(config_path),
        "--cost-profile",
        str(zero_path),
        "--synthetic-bars",
        str(bars_path),
        "--runs-directory",
        str(tmp_path / "runs"),
    ]
    assert main(zero_args) == 1
    assert "allow_zero_costs=true" in capsys.readouterr().err
    cfg["allow_zero_costs"] = True
    config_path.write_text(json.dumps(cfg))
    assert main(zero_args) == 0
    assert "Zero transaction costs explicitly allowed" in capsys.readouterr().out
