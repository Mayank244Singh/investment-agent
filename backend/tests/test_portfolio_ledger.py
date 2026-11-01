"""Append-only FIFO portfolio ledger tests."""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models.ledger import (
    HoldingLot,
    LedgerTransaction,
    LotRealization,
    ProfitVaultEntry,
)
from app.modules.portfolio import (
    InsufficientHoldingsError,
    LedgerService,
)

TRADE_TIME = datetime(2026, 11, 1, 4, 30, tzinfo=UTC)


@pytest.fixture
def session() -> Generator[Session, None, None]:
    """Provide a fresh in-memory ledger database."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    database_session = session_factory()
    try:
        yield database_session
    finally:
        database_session.close()
        engine.dispose()


def test_records_deposit_withdrawal_and_buy_cost_basis(session: Session) -> None:
    ledger = LedgerService(session)

    ledger.record_deposit(100_000, TRADE_TIME)
    ledger.record_withdrawal(10_000, TRADE_TIME + timedelta(minutes=1))
    buy = ledger.record_buy(
        "infy", 10, 1_000, TRADE_TIME + timedelta(minutes=2), fees_paise=100
    )

    summary = ledger.get_summary()
    assert buy.total_cost_paise == 10_100
    assert summary.cash_balance_paise == 79_900
    assert summary.open_cost_basis_paise == 10_100
    assert summary.realized_pnl_paise == 0
    assert summary.profit_vault_balance_paise == 0
    assert summary.holdings[0].symbol == "INFY"
    assert summary.holdings[0].quantity == 10


def test_partial_sell_allocates_cost_and_credits_profit_vault(
    session: Session,
) -> None:
    ledger = LedgerService(session)
    ledger.record_deposit(100_000, TRADE_TIME)
    ledger.record_buy("INFY", 10, 1_000, TRADE_TIME, fees_paise=100)

    sale = ledger.record_sell(
        "INFY", 4, 1_500, TRADE_TIME + timedelta(days=1), fees_paise=40
    )

    lot = session.scalar(select(HoldingLot))
    realization = session.scalar(select(LotRealization))
    summary = ledger.get_summary()
    assert lot is not None
    assert realization is not None
    assert lot.remaining_quantity == 6
    assert lot.remaining_cost_paise == 6_060
    assert realization.cost_basis_paise == 4_040
    assert realization.net_proceeds_paise == 5_960
    assert sale.realized_pnl_paise == 1_920
    assert sale.profit_vault_credit_paise == 1_920
    assert summary.profit_vault_balance_paise == 1_920
    assert summary.deployable_cash_paise == 93_940


def test_buy_and_sell_fees_affect_cost_basis_proceeds_and_profit(
    session: Session,
) -> None:
    ledger = LedgerService(session)
    ledger.record_deposit(20_000, TRADE_TIME)
    ledger.record_buy("INFY", 4, 1_000, TRADE_TIME, fees_paise=400)

    sale = ledger.record_sell(
        "INFY", 2, 1_500, TRADE_TIME + timedelta(days=1), fees_paise=200
    )

    lot = session.scalar(select(HoldingLot))
    realization = session.scalar(select(LotRealization))
    summary = ledger.get_summary()
    assert lot is not None
    assert realization is not None
    assert realization.cost_basis_paise == 2_200
    assert realization.gross_proceeds_paise == 3_000
    assert realization.sell_fees_paise == 200
    assert realization.net_proceeds_paise == 2_800
    assert sale.realized_pnl_paise == 600
    assert sale.profit_vault_credit_paise == 600
    assert lot.remaining_cost_paise == 2_200
    assert summary.cash_balance_paise == 18_400
    assert summary.profit_vault_balance_paise == 600


def test_sell_uses_fifo_across_multiple_lots(session: Session) -> None:
    ledger = LedgerService(session)
    ledger.record_deposit(100_000, TRADE_TIME)
    first_buy = ledger.record_buy("TCS", 5, 1_000, TRADE_TIME, fees_paise=5)
    second_buy = ledger.record_buy(
        "TCS", 5, 2_000, TRADE_TIME + timedelta(minutes=1), fees_paise=5
    )

    ledger.record_sell(
        "TCS", 7, 3_000, TRADE_TIME + timedelta(days=1), fees_paise=7
    )

    lots = list(session.scalars(select(HoldingLot).order_by(HoldingLot.id)))
    realizations = list(
        session.scalars(select(LotRealization).order_by(LotRealization.id))
    )
    assert lots[0].id == first_buy.holding_lot_id
    assert lots[0].remaining_quantity == 0
    assert lots[0].remaining_cost_paise == 0
    assert lots[1].id == second_buy.holding_lot_id
    assert lots[1].remaining_quantity == 3
    assert lots[1].remaining_cost_paise == 6_003
    assert [item.quantity for item in realizations] == [5, 2]
    assert [item.cost_basis_paise for item in realizations] == [5_005, 4_002]


def test_selling_more_than_held_is_rejected_without_partial_writes(
    session: Session,
) -> None:
    ledger = LedgerService(session)
    ledger.record_deposit(10_000, TRADE_TIME)
    ledger.record_buy("RELIANCE", 3, 1_000, TRADE_TIME)

    with pytest.raises(InsufficientHoldingsError, match="only 3 held"):
        ledger.record_sell("RELIANCE", 4, 1_200, TRADE_TIME + timedelta(days=1))

    sell_count = session.scalar(
        select(func.count()).select_from(LedgerTransaction).where(
            LedgerTransaction.transaction_type == "sell"
        )
    )
    assert sell_count == 0
    assert session.scalar(select(func.count()).select_from(LotRealization)) == 0
    assert session.scalar(select(func.count()).select_from(ProfitVaultEntry)) == 0


def test_loss_does_not_credit_or_reduce_existing_profit_vault(
    session: Session,
) -> None:
    ledger = LedgerService(session)
    ledger.record_deposit(100_000, TRADE_TIME)
    ledger.record_buy("HDFCBANK", 2, 1_000, TRADE_TIME)
    profit = ledger.record_sell(
        "HDFCBANK", 1, 1_500, TRADE_TIME + timedelta(days=1)
    )
    ledger.record_buy("SBIN", 2, 1_000, TRADE_TIME + timedelta(days=2))

    loss = ledger.record_sell(
        "SBIN", 2, 800, TRADE_TIME + timedelta(days=3), fees_paise=100
    )

    summary = ledger.get_summary()
    assert profit.realized_pnl_paise == 500
    assert loss.realized_pnl_paise == -500
    assert loss.profit_vault_credit_paise == 0
    assert summary.realized_pnl_paise == 0
    assert summary.profit_vault_balance_paise == 500
    assert session.scalar(select(func.count()).select_from(ProfitVaultEntry)) == 1


def test_sell_rolls_back_transaction_lot_match_realization_and_vault_on_failure(
    session: Session,
) -> None:
    ledger = LedgerService(session)
    ledger.record_deposit(20_000, TRADE_TIME)
    ledger.record_buy("INFY", 4, 1_000, TRADE_TIME, fees_paise=400)

    def fail_before_vault_flush(
        db_session: Session, _flush_context: object, _instances: object
    ) -> None:
        if any(isinstance(item, ProfitVaultEntry) for item in db_session.new):
            raise RuntimeError("simulated vault persistence failure")

    event.listen(session, "before_flush", fail_before_vault_flush)
    try:
        with pytest.raises(RuntimeError, match="simulated vault persistence failure"):
            ledger.record_sell(
                "INFY",
                2,
                1_500,
                TRADE_TIME + timedelta(days=1),
                fees_paise=200,
            )
    finally:
        event.remove(session, "before_flush", fail_before_vault_flush)

    lot = session.scalar(select(HoldingLot))
    assert lot is not None
    assert lot.remaining_quantity == 4
    assert lot.remaining_cost_paise == 4_400
    sell_count = session.scalar(
        select(func.count()).select_from(LedgerTransaction).where(
            LedgerTransaction.transaction_type == "sell"
        )
    )
    assert sell_count == 0
    assert session.scalar(select(func.count()).select_from(LotRealization)) == 0
    assert session.scalar(select(func.count()).select_from(ProfitVaultEntry)) == 0
