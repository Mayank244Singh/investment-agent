"""Independent fail-closed risk validator tests."""

from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.modules.risk import (
    HoldingSnapshot,
    PortfolioSnapshot,
    ProposedTrade,
    RealizedPnlEvent,
    RejectionCode,
    RiskLimits,
    RiskValidator,
    ValidationResult,
)

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


def default_limits() -> RiskLimits:
    return RiskLimits.from_settings(Settings())


def snapshot(
    *,
    cash: int = 100_000,
    vault: int = 0,
    open_cost: int = 0,
    holdings: Mapping[str, HoldingSnapshot] | None = None,
    events: tuple[RealizedPnlEvent, ...] = (),
) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        cash_balance_paise=cash,
        profit_vault_balance_paise=vault,
        open_cost_basis_paise=open_cost,
        holdings={} if holdings is None else holdings,
        realized_events=events,
    )


def buy(**changes: object) -> ProposedTrade:
    values: dict[str, object] = {
        "side": "buy",
        "symbol": "INFY",
        "quantity": 5,
        "price_paise": 1_000,
        "fees_paise": 0,
        "stop_loss_paise": 900,
        "proposed_at_utc": NOW,
    }
    values.update(changes)
    return ProposedTrade(**values)  # type: ignore[arg-type]


def codes(result: ValidationResult) -> set[RejectionCode]:
    return {reason.code for reason in result.reasons}


def test_valid_buy_is_approved() -> None:
    result = RiskValidator(default_limits()).validate(buy(), snapshot())
    assert result.approved
    assert result.reasons == ()


@pytest.mark.parametrize(
    ("trade", "code"),
    [
        (buy(side="hold"), RejectionCode.INVALID_SIDE),
        (buy(symbol=""), RejectionCode.INVALID_SYMBOL),
        (buy(quantity=0), RejectionCode.INVALID_QUANTITY),
        (buy(price_paise=0), RejectionCode.INVALID_PRICE),
        (buy(fees_paise=-1), RejectionCode.INVALID_FEES),
        (
            buy(proposed_at_utc=datetime(2026, 11, 1, 10, 0)),
            RejectionCode.INVALID_TIMESTAMP,
        ),
    ],
)
def test_quantity_price_and_fee_sanity_rules(
    trade: ProposedTrade, code: RejectionCode
) -> None:
    result = RiskValidator(default_limits()).validate(trade, snapshot())
    assert not result.approved
    assert code in codes(result)


def test_insufficient_cash_rule() -> None:
    result = RiskValidator(default_limits()).validate(
        buy(quantity=10, price_paise=1_000), snapshot(cash=9_999)
    )
    assert RejectionCode.INSUFFICIENT_CASH in codes(result)


def test_max_order_size_rule() -> None:
    result = RiskValidator(default_limits()).validate(
        buy(quantity=11, price_paise=1_000), snapshot()
    )
    assert RejectionCode.MAX_ORDER_SIZE in codes(result)


def test_max_single_stock_allocation_rule() -> None:
    current = {"INFY": HoldingSnapshot(quantity=15, cost_basis_paise=15_000)}
    result = RiskValidator(default_limits()).validate(
        buy(quantity=6, price_paise=1_000),
        snapshot(cash=85_000, open_cost=15_000, holdings=current),
    )
    assert RejectionCode.MAX_STOCK_ALLOCATION in codes(result)


@pytest.mark.parametrize(
    ("stop_loss", "code"),
    [
        (None, RejectionCode.STOP_LOSS_REQUIRED),
        (0, RejectionCode.INVALID_STOP_LOSS),
        (1_000, RejectionCode.INVALID_STOP_LOSS),
    ],
)
def test_stop_loss_rules(stop_loss: int | None, code: RejectionCode) -> None:
    result = RiskValidator(default_limits()).validate(
        buy(stop_loss_paise=stop_loss), snapshot()
    )
    assert code in codes(result)


def test_max_loss_per_trade_rule() -> None:
    result = RiskValidator(default_limits()).validate(
        buy(quantity=10, price_paise=1_000, stop_loss_paise=800), snapshot()
    )
    assert RejectionCode.MAX_LOSS_PER_TRADE in codes(result)


def test_max_daily_loss_uses_asia_kolkata_midnight() -> None:
    before_midnight = RealizedPnlEvent(
        realized_pnl_paise=-5_000,
        realized_at_utc=datetime(2026, 11, 1, 18, 29, tzinfo=UTC),
    )
    after_midnight = RealizedPnlEvent(
        realized_pnl_paise=-2_000,
        realized_at_utc=datetime(2026, 11, 1, 18, 31, tzinfo=UTC),
    )
    proposed_after_midnight = datetime(2026, 11, 1, 18, 32, tzinfo=UTC)

    result = RiskValidator(default_limits()).validate(
        buy(proposed_at_utc=proposed_after_midnight),
        snapshot(events=(before_midnight, after_midnight)),
    )
    assert RejectionCode.MAX_DAILY_LOSS in codes(result)


def test_sell_more_than_held_is_rejected_and_stop_loss_is_not_required() -> None:
    trade = ProposedTrade("sell", "INFY", 6, 1_000, NOW)
    held = {"INFY": HoldingSnapshot(quantity=5, cost_basis_paise=5_000)}
    result = RiskValidator(default_limits()).validate(
        trade, snapshot(cash=95_000, open_cost=5_000, holdings=held)
    )
    assert codes(result) == {RejectionCode.INSUFFICIENT_HOLDINGS}


def test_multiple_simultaneous_failures_are_all_returned() -> None:
    result = RiskValidator(default_limits()).validate(
        buy(quantity=20, price_paise=1_000, stop_loss_paise=None),
        snapshot(cash=5_000),
    )
    assert {
        RejectionCode.INSUFFICIENT_CASH,
        RejectionCode.MAX_ORDER_SIZE,
        RejectionCode.STOP_LOSS_REQUIRED,
    }.issubset(codes(result))


def test_missing_config_fails_closed_and_lists_every_missing_limit() -> None:
    missing = RiskLimits(None, None, None, None, None)
    result = RiskValidator(missing).validate(buy(), snapshot())
    assert not result.approved
    assert [reason.code for reason in result.reasons].count(
        RejectionCode.MISSING_CONFIG
    ) == 5


def test_settings_supply_the_approved_default_limits() -> None:
    limits = default_limits()
    assert limits.max_order_percent == Decimal("10")
    assert limits.max_stock_allocation_percent == Decimal("20")
    assert limits.max_loss_per_trade_percent == Decimal("1")
    assert limits.max_daily_loss_percent == Decimal("2")
    assert limits.daily_loss_timezone == "Asia/Kolkata"


def test_invalid_config_is_rejected_during_settings_startup() -> None:
    with pytest.raises(ValidationError):
        Settings(risk_max_order_percent=Decimal("0"))
    with pytest.raises(ValidationError):
        Settings(risk_max_daily_loss_percent=Decimal("101"))


def test_unexpected_rule_error_fails_closed() -> None:
    class ExplodingHoldings(Mapping[str, HoldingSnapshot]):
        def __getitem__(self, key: str) -> HoldingSnapshot:
            raise RuntimeError(key)

        def __iter__(self) -> Iterator[str]:
            return iter(())

        def __len__(self) -> int:
            return 0

    result = RiskValidator(default_limits()).validate(
        buy(), snapshot(holdings=ExplodingHoldings())
    )
    assert codes(result) == {RejectionCode.VALIDATION_ERROR}
