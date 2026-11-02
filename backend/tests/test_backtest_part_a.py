"""Synthetic-only Phase 5 simulation checks; no real database is opened."""

import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.prices import DailyPrice, ImportBatch, Instrument
from app.modules.backtest.costs import CostProfile, charges, slipped_price
from app.modules.backtest.data import Bar, PriceData, load_stored_prices
from app.modules.backtest.engine import Intent, SimulationConfig, simulate
from app.modules.portfolio.ledger_service import LedgerService
from app.modules.risk.models import (
    HoldingSnapshot,
    PortfolioSnapshot,
    ProposedTrade,
    RealizedPnlEvent,
    RiskLimits,
)
from app.modules.risk.validator import RiskValidator

D1 = date(2026, 1, 1)
D2 = date(2026, 1, 2)
D3 = date(2026, 1, 3)


def profile(**changes: object) -> CostProfile:
    base = CostProfile(
        "SYNTHETIC_TEST_BROKER",
        D1,
        "invented test charges",
        False,
        Decimal("10"),
        Decimal("5"),
        Decimal("7"),
        Decimal("2"),
        Decimal("1"),
        Decimal("3"),
        Decimal("1800"),
        17,
        Decimal("0"),
    )
    return replace(base, **cast(Any, changes))


def config(**changes: object) -> SimulationConfig:
    base = SimulationConfig(
        initial_capital_paise=1_000_000,
        universe=("TESTA", "TESTB"),
        universe_as_of=D1,
        risk_limits=RiskLimits(
            Decimal(100), Decimal(100), Decimal(100), Decimal(100), "Asia/Kolkata"
        ),
        max_previous_volume_percent=Decimal(100),
    )
    return replace(base, **cast(Any, changes))


def bar(
    day: date,
    *,
    symbol: str = "TESTA",
    opened: int = 1000,
    low: int = 950,
    high: int = 1100,
    closed: int = 1000,
    previous: int = 1000,
    volume: int = 1000,
) -> Bar:
    return Bar(symbol, day, opened, high, low, closed, previous, volume)


def data(*bars: Bar) -> PriceData:
    return PriceData(bars, origin="synthetic")


def buy_once(view: object, summary: object) -> tuple[Intent, ...]:
    if view.as_of == D1:  # type: ignore[attr-defined]
        return (Intent("buy", "TESTA", 2, 900),)
    return ()


def test_no_look_ahead_and_next_open_fill() -> None:
    seen: list[date] = []

    def strategy(view: object, summary: object) -> tuple[Intent, ...]:
        seen.append(view.as_of)  # type: ignore[attr-defined]
        if view.as_of == D1:  # type: ignore[attr-defined]
            with pytest.raises(ValueError, match="future"):
                view.get("TESTA", D2)  # type: ignore[attr-defined]
        return buy_once(view, summary)

    # The explicit future read must fail, never return the next bar.
    view = data(bar(D1), bar(D2)).view(D1)
    with pytest.raises(ValueError, match="future"):
        view.get("TESTA", D2)
    result = simulate(
        data(bar(D1), bar(D2, opened=1050, low=1000, high=1100)),
        profile(),
        config(),
        strategy,
    )
    assert seen == [D1, D2]
    assert [(fill.day, fill.price_paise) for fill in result.fills] == [(D2, 1050)]


def test_each_cost_component_and_half_up_rounding() -> None:
    buy = charges(profile(), "buy", 10, 10_000)
    assert (
        buy.brokerage_paise,
        buy.stt_paise,
        buy.exchange_paise,
        buy.regulator_paise,
        buy.stamp_paise,
        buy.gst_paise,
        buy.depository_paise,
    ) == (100, 50, 20, 10, 30, 23, 0)
    sell = charges(profile(), "sell", 10, 10_000)
    assert (
        sell.brokerage_paise,
        sell.stt_paise,
        sell.exchange_paise,
        sell.regulator_paise,
        sell.stamp_paise,
        sell.gst_paise,
        sell.depository_paise,
    ) == (100, 70, 20, 10, 0, 23, 17)
    assert (
        charges(profile(brokerage_bps=Decimal("0.5")), "buy", 1, 100).brokerage_paise
        == 0
    )
    assert (
        charges(profile(brokerage_bps=Decimal("50")), "buy", 1, 100).brokerage_paise
        == 1
    )


def test_placeholder_profile_is_dated_broker_named_and_unverified() -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "config"
        / ("backtest-cost-placeholder-2026-11-02.json")
    )
    loaded = CostProfile.from_json(path)
    assert loaded.broker == "UNVERIFIED_PLACEHOLDER"
    assert loaded.rates_as_of == date(2026, 11, 2)
    assert not loaded.verified
    assert loaded.source_note


def test_adverse_slippage() -> None:
    p = profile(slippage_bps=Decimal("100"))
    assert slipped_price(p, "buy", 1000) == 1010
    assert slipped_price(p, "sell", 1000) == 990
    result = simulate(data(bar(D1), bar(D2, opened=1000)), p, config(), buy_once)
    assert result.fills[0].price_paise == 1010


@pytest.mark.parametrize("opened,low,expected", [(1000, 880, 900), (850, 800, 850)])
def test_stop_and_gap_through_stop(opened: int, low: int, expected: int) -> None:
    bars = data(
        bar(D1),
        bar(D2, opened=1000, low=950),
        bar(D3, opened=opened, low=low, high=1100, previous=1000),
    )
    result = simulate(bars, profile(), config(), buy_once)
    assert result.fills[-1].reason == "stop"
    assert result.fills[-1].price_paise == expected
    assert result.summary.holdings == ()


def test_risk_rejection_and_previous_volume_cap() -> None:
    limited = config(
        risk_limits=RiskLimits(
            Decimal("0.01"), Decimal(100), Decimal(100), Decimal(100), "Asia/Kolkata"
        ),
        max_previous_volume_percent=Decimal("1"),
    )
    result = simulate(
        data(bar(D1, volume=100), bar(D2)),
        profile(),
        limited,
        lambda view, summary: (
            (Intent("buy", "TESTA", 5, 900),) if view.as_of == D1 else ()
        ),
    )
    assert result.reductions[0].unfilled == 4
    assert any(item.reason == "max_order_size" for item in result.rejections)
    assert result.summary.holdings == ()


def test_sell_bypasses_daily_loss_but_checks_quantity() -> None:
    limits = RiskLimits(
        Decimal(10), Decimal(20), Decimal(1), Decimal(2), "Asia/Kolkata"
    )
    snapshot = PortfolioSnapshot(
        100_000,
        0,
        100_000,
        {"TESTA": HoldingSnapshot(10, 100_000)},
        (RealizedPnlEvent(-50_000, datetime(2026, 1, 2, 4, tzinfo=UTC)),),
    )
    trade = ProposedTrade(
        "sell", "TESTA", 10, 1000, datetime(2026, 1, 2, 5, tzinfo=UTC)
    )
    assert RiskValidator(limits).validate(trade, snapshot).approved
    assert (
        not RiskValidator(limits)
        .validate(replace(trade, quantity=11), snapshot)
        .approved
    )


def test_fifo_matches_real_ledger_service() -> None:
    # The simulation uses precisely this service in a private in-memory DB.
    p = profile(
        brokerage_bps=Decimal(0),
        stt_buy_bps=Decimal(0),
        stt_sell_bps=Decimal(0),
        exchange_bps=Decimal(0),
        regulator_bps=Decimal(0),
        stamp_buy_bps=Decimal(0),
        gst_bps=Decimal(0),
        depository_sell_paise=0,
    )

    def strategy(view: object, summary: object) -> tuple[Intent, ...]:
        if view.as_of == D1:  # type: ignore[attr-defined]
            return (Intent("buy", "TESTA", 2, 800),)
        if view.as_of == D2:  # type: ignore[attr-defined]
            return (Intent("sell", "TESTA", 1),)
        return ()

    result = simulate(
        data(
            bar(D1),
            bar(D2, opened=1000, low=900),
            bar(D3, opened=1200, low=1000, high=1200, previous=1000),
        ),
        p,
        replace(config(), allow_zero_costs=True),
        strategy,
    )
    assert result.summary.realized_pnl_paise == 200
    assert result.summary.holdings[0].cost_basis_paise == 1000
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ledger = LedgerService(session)
        ledger.record_deposit(1_000_000, datetime(2026, 1, 1, tzinfo=UTC))
        ledger.record_buy("TESTA", 2, 1000, datetime(2026, 1, 2, tzinfo=UTC))
        ledger.record_sell("TESTA", 1, 1200, datetime(2026, 1, 3, tzinfo=UTC))
        assert (
            result.summary.realized_pnl_paise == ledger.get_summary().realized_pnl_paise
        )
        assert (
            result.summary.open_cost_basis_paise
            == ledger.get_summary().open_cost_basis_paise
        )


def test_corporate_action_stops_or_excludes_from_flagged_date() -> None:
    bars = data(bar(D1), bar(D2, previous=900))
    with pytest.raises(ValueError, match="corporate action"):
        simulate(bars, profile(), config(), lambda view, summary: ())
    result = simulate(
        bars,
        profile(),
        config(corporate_action_mode="exclude"),
        lambda view, summary: (),
    )
    assert any("excluded" in item for item in result.warnings)
    assert result.last_marks == (("TESTA", D1, 1000, False),)
    held_bars = data(bar(D1), bar(D2), bar(D3, previous=900))
    with pytest.raises(ValueError, match="corporate action"):
        simulate(
            held_bars, profile(), config(corporate_action_mode="exclude"), buy_once
        )


def test_missing_held_bar_is_stale_and_no_trade() -> None:
    bars = data(bar(D1), bar(D2), bar(D3, symbol="TESTB"))
    result = simulate(bars, profile(), config(), buy_once)
    assert result.last_marks[0] == ("TESTA", D2, 1000, True)
    assert any("missing held bar" in item for item in result.warnings)


def test_stored_db_unchanged_and_unverified_refused(tmp_path: Path) -> None:
    path = tmp_path / "source.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        instrument = Instrument(symbol="TESTA", series="EQ", isin=None)
        batch = ImportBatch(
            file_name="synthetic.csv",
            file_sha256="a" * 64,
            source_label="synthetic",
            total_rows=1,
            imported_rows=1,
            skipped_rows=0,
            rejected_rows=0,
            status="completed",
        )
        session.add_all((instrument, batch))
        session.flush()
        session.add(
            DailyPrice(
                instrument_id=instrument.id,
                import_batch_id=batch.id,
                trade_date=D1,
                open_paise=1000,
                high_paise=1100,
                low_paise=900,
                close_paise=1000,
                previous_close_paise=1000,
                volume=1000,
            )
        )
        session.commit()
    engine.dispose()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    stored = load_stored_prices(path, ("TESTA",))
    with pytest.raises(ValueError, match="unverified"):
        simulate(stored, profile(), config(), lambda view, summary: ())
    simulate(
        stored,
        profile(verified=True, slippage_bps=Decimal("1")),
        config(),
        lambda view, summary: (),
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_broker_calculator_golden_example_pending() -> None:
    """Use a supplied delivery calculator breakdown when rates are verified."""
    default = (
        Path(__file__).resolve().parents[1]
        / "config"
        / ("backtest-cost-placeholder-2026-11-02.json")
    )
    profile_path = Path(os.environ.get("BACKTEST_VERIFIED_PROFILE", str(default)))
    broker_profile = CostProfile.from_json(profile_path)
    if not broker_profile.verified:
        pytest.skip("Awaiting verified broker charge breakdown and dated rates")
    golden_path = os.environ.get("BACKTEST_BROKER_GOLDEN")
    if golden_path is None:
        pytest.fail("Set BACKTEST_BROKER_GOLDEN for a verified cost profile")
    examples = json.loads(Path(golden_path).read_text())
    for side in ("buy", "sell"):
        example = examples[side]
        actual = charges(
            broker_profile, side, example["quantity"], example["price_paise"]
        )
        for component, expected in example["components"].items():
            assert abs(asdict(actual)[component] - expected) <= 100
        assert abs(actual.total_paise - example["total_paise"]) <= 100
