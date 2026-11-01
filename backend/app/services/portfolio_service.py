"""Portfolio ledger API orchestration."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.ledger import (
    CapitalMovement,
    HoldingLot,
    LedgerTransaction,
    LotRealization,
    ProfitVaultEntry,
)
from app.modules.portfolio import LedgerService
from app.schemas.portfolio import (
    CapitalMovementCreate,
    CapitalMovementResponse,
    HoldingLotResponse,
    HoldingResponse,
    LedgerResponse,
    LedgerSummaryResponse,
    LedgerTransactionResponse,
    LotRealizationResponse,
    ProfitVaultEntryResponse,
    TradeCreate,
)


def record_deposit(
    session: Session,
    request: CapitalMovementCreate,
    idempotency_key: str | None,
) -> CapitalMovementResponse:
    """Persist one historical deposit and return its immutable record."""
    result = LedgerService(session).record_deposit(
        request.amount_paise,
        request.occurred_at_utc,
        request.note,
        idempotency_key,
    )
    movement = session.get(CapitalMovement, result.movement_id)
    if movement is None:
        raise RuntimeError("recorded deposit could not be loaded")
    return _capital_movement_response(movement)


def record_withdrawal(
    session: Session,
    request: CapitalMovementCreate,
    idempotency_key: str | None,
) -> CapitalMovementResponse:
    """Persist one historical withdrawal and return its immutable record."""
    result = LedgerService(session).record_withdrawal(
        request.amount_paise,
        request.occurred_at_utc,
        request.note,
        idempotency_key,
    )
    movement = session.get(CapitalMovement, result.movement_id)
    if movement is None:
        raise RuntimeError("recorded withdrawal could not be loaded")
    return _capital_movement_response(movement)


def record_buy(
    session: Session, request: TradeCreate, idempotency_key: str | None
) -> LedgerTransactionResponse:
    """Persist one historical buy and return its immutable record."""
    result = LedgerService(session).record_buy(
        request.symbol,
        request.quantity,
        request.price_paise,
        request.transacted_at_utc,
        request.fees_paise,
        request.note,
        idempotency_key,
    )
    transaction = session.get(LedgerTransaction, result.transaction_id)
    if transaction is None:
        raise RuntimeError("recorded buy could not be loaded")
    return _transaction_response(transaction)


def record_sell(
    session: Session, request: TradeCreate, idempotency_key: str | None
) -> LedgerTransactionResponse:
    """Persist one historical sell and return its immutable record."""
    result = LedgerService(session).record_sell(
        request.symbol,
        request.quantity,
        request.price_paise,
        request.transacted_at_utc,
        request.fees_paise,
        request.note,
        idempotency_key,
    )
    transaction = session.get(LedgerTransaction, result.transaction_id)
    if transaction is None:
        raise RuntimeError("recorded sell could not be loaded")
    return _transaction_response(transaction)


def read_ledger(session: Session) -> LedgerResponse:
    """Return the append-only ledger and its derived book-cost summary."""
    summary = LedgerService(session).get_summary()
    movements = session.scalars(
        select(CapitalMovement).order_by(
            CapitalMovement.occurred_at_utc, CapitalMovement.id
        )
    )
    transactions = session.scalars(
        select(LedgerTransaction).order_by(
            LedgerTransaction.transacted_at_utc, LedgerTransaction.id
        )
    )
    lots = session.scalars(
        select(HoldingLot).order_by(HoldingLot.acquired_at_utc, HoldingLot.id)
    )
    realizations = session.scalars(
        select(LotRealization).order_by(
            LotRealization.realized_at_utc, LotRealization.id
        )
    )
    vault_entries = session.scalars(
        select(ProfitVaultEntry).order_by(ProfitVaultEntry.id)
    )
    return LedgerResponse(
        summary=LedgerSummaryResponse(
            cash_balance_paise=summary.cash_balance_paise,
            deployable_cash_paise=summary.deployable_cash_paise,
            open_cost_basis_paise=summary.open_cost_basis_paise,
            realized_pnl_paise=summary.realized_pnl_paise,
            profit_vault_balance_paise=summary.profit_vault_balance_paise,
            risk_capital_paise=summary.risk_capital_paise,
        ),
        holdings=[
            HoldingResponse(
                symbol=item.symbol,
                quantity=item.quantity,
                cost_basis_paise=item.cost_basis_paise,
            )
            for item in summary.holdings
        ],
        capital_movements=[
            _capital_movement_response(item)
            for item in movements
        ],
        transactions=[
            _transaction_response(item)
            for item in transactions
        ],
        lots=[
            HoldingLotResponse(
                id=item.id,
                buy_transaction_id=item.buy_transaction_id,
                symbol=item.symbol,
                acquired_at_utc=_as_utc(item.acquired_at_utc),
                original_quantity=item.original_quantity,
                remaining_quantity=item.remaining_quantity,
                total_cost_paise=item.total_cost_paise,
                remaining_cost_paise=item.remaining_cost_paise,
            )
            for item in lots
        ],
        realizations=[
            LotRealizationResponse(
                id=item.id,
                sell_transaction_id=item.sell_transaction_id,
                holding_lot_id=item.holding_lot_id,
                quantity=item.quantity,
                cost_basis_paise=item.cost_basis_paise,
                gross_proceeds_paise=item.gross_proceeds_paise,
                sell_fees_paise=item.sell_fees_paise,
                net_proceeds_paise=item.net_proceeds_paise,
                realized_pnl_paise=item.realized_pnl_paise,
                realized_at_utc=_as_utc(item.realized_at_utc),
            )
            for item in realizations
        ],
        profit_vault_entries=[
            ProfitVaultEntryResponse(
                id=item.id,
                sell_transaction_id=item.sell_transaction_id,
                amount_paise=item.amount_paise,
                created_at_utc=_as_utc(item.created_at_utc),
            )
            for item in vault_entries
        ],
    )


def _capital_movement_response(item: CapitalMovement) -> CapitalMovementResponse:
    return CapitalMovementResponse(
        id=item.id,
        movement_type=item.movement_type,
        amount_paise=item.amount_paise,
        occurred_at_utc=_as_utc(item.occurred_at_utc),
        note=item.note,
        idempotency_key=item.idempotency_key,
    )


def _transaction_response(item: LedgerTransaction) -> LedgerTransactionResponse:
    return LedgerTransactionResponse(
        id=item.id,
        transaction_type=item.transaction_type,
        symbol=item.symbol,
        quantity=item.quantity,
        price_paise=item.price_paise,
        fees_paise=item.fees_paise,
        transacted_at_utc=_as_utc(item.transacted_at_utc),
        note=item.note,
        idempotency_key=item.idempotency_key,
    )


def _as_utc(value: datetime) -> datetime:
    """Restore UTC information omitted by SQLite's datetime adapter."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
