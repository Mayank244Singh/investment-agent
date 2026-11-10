"""Atomic append-only portfolio ledger operations."""

from dataclasses import dataclass
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
from app.modules.portfolio.errors import (
    IdempotencyConflictError,
    InsufficientCashError,
    InsufficientHoldingsError,
    LedgerValidationError,
)


@dataclass(frozen=True)
class CapitalMovementResult:
    """Result of recording a deposit or withdrawal."""

    movement_id: int
    cash_balance_paise: int


@dataclass(frozen=True)
class BuyResult:
    """Result of recording a buy and opening its FIFO lot."""

    transaction_id: int
    holding_lot_id: int
    total_cost_paise: int


@dataclass(frozen=True)
class SellResult:
    """Result of recording a sale and its FIFO realizations."""

    transaction_id: int
    realized_pnl_paise: int
    profit_vault_credit_paise: int


@dataclass(frozen=True)
class HoldingSummary:
    """A symbol's quantity and cost basis without a market valuation."""

    symbol: str
    quantity: int
    cost_basis_paise: int


@dataclass(frozen=True)
class PortfolioSummary:
    """Ledger-derived balances; deliberately excludes unrealised P&L."""

    cash_balance_paise: int
    deployable_cash_paise: int
    open_cost_basis_paise: int
    realized_pnl_paise: int
    profit_vault_balance_paise: int
    risk_capital_paise: int
    holdings: tuple[HoldingSummary, ...]


class LedgerService:
    """Persist immutable ledger events and derive portfolio balances."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_deposit(
        self,
        amount_paise: int,
        occurred_at: datetime,
        note: str | None = None,
        idempotency_key: str | None = None,
        *,
        commit: bool = True,
    ) -> CapitalMovementResult:
        """Add original capital to the ledger."""
        return self._record_capital_movement(
            "deposit", amount_paise, occurred_at, note, idempotency_key, commit
        )

    def record_withdrawal(
        self,
        amount_paise: int,
        occurred_at: datetime,
        note: str | None = None,
        idempotency_key: str | None = None,
    ) -> CapitalMovementResult:
        """Record a historical cash withdrawal when total cash covers it."""
        return self._record_capital_movement(
            "withdrawal", amount_paise, occurred_at, note, idempotency_key
        )

    def record_buy(
        self,
        symbol: str,
        quantity: int,
        price_paise: int,
        transacted_at: datetime,
        fees_paise: int = 0,
        note: str | None = None,
        idempotency_key: str | None = None,
        *,
        commit: bool = True,
    ) -> BuyResult:
        """Record a buy and create one FIFO holding lot."""
        normalized_symbol = self._validate_trade_input(
            symbol, quantity, price_paise, fees_paise, transacted_at
        )
        normalized_key = self._normalize_idempotency_key(idempotency_key)
        total_cost_paise = quantity * price_paise + fees_paise
        existing = self._find_transaction(normalized_key)
        if existing is not None:
            self._ensure_transaction_replay(
                existing,
                "buy",
                normalized_symbol,
                quantity,
                price_paise,
                fees_paise,
                transacted_at,
                note,
            )
            lot = self._session.scalar(
                select(HoldingLot).where(
                    HoldingLot.buy_transaction_id == existing.id
                )
            )
            if lot is None:
                raise RuntimeError("idempotent buy is missing its holding lot")
            return BuyResult(existing.id, lot.id, lot.total_cost_paise)

        transaction = LedgerTransaction(
            transaction_type="buy",
            symbol=normalized_symbol,
            quantity=quantity,
            price_paise=price_paise,
            fees_paise=fees_paise,
            transacted_at_utc=self._as_utc(transacted_at),
            note=note,
            idempotency_key=normalized_key,
        )
        try:
            self._session.add(transaction)
            self._session.flush()
            lot = HoldingLot(
                buy_transaction_id=transaction.id,
                symbol=normalized_symbol,
                acquired_at_utc=transaction.transacted_at_utc,
                original_quantity=quantity,
                remaining_quantity=quantity,
                total_cost_paise=total_cost_paise,
                remaining_cost_paise=total_cost_paise,
            )
            self._session.add(lot)
            self._session.flush()
            result = BuyResult(transaction.id, lot.id, total_cost_paise)
            if commit:
                self._session.commit()
            return result
        except Exception:
            self._session.rollback()
            raise

    def record_sell(
        self,
        symbol: str,
        quantity: int,
        price_paise: int,
        transacted_at: datetime,
        fees_paise: int = 0,
        note: str | None = None,
        idempotency_key: str | None = None,
        *,
        commit: bool = True,
    ) -> SellResult:
        """Atomically match FIFO lots, realise P&L, and credit positive profit."""
        normalized_symbol = self._validate_trade_input(
            symbol, quantity, price_paise, fees_paise, transacted_at
        )
        normalized_key = self._normalize_idempotency_key(idempotency_key)
        existing = self._find_transaction(normalized_key)
        if existing is not None:
            self._ensure_transaction_replay(
                existing,
                "sell",
                normalized_symbol,
                quantity,
                price_paise,
                fees_paise,
                transacted_at,
                note,
            )
            realizations = self._session.scalars(
                select(LotRealization).where(
                    LotRealization.sell_transaction_id == existing.id
                )
            )
            realized_pnl = sum(item.realized_pnl_paise for item in realizations)
            vault_entry = self._session.scalar(
                select(ProfitVaultEntry).where(
                    ProfitVaultEntry.sell_transaction_id == existing.id
                )
            )
            vault_credit = vault_entry.amount_paise if vault_entry is not None else 0
            return SellResult(existing.id, realized_pnl, vault_credit)
        gross_proceeds_paise = quantity * price_paise
        if fees_paise > gross_proceeds_paise:
            raise LedgerValidationError("fees_paise cannot exceed gross proceeds")

        lots = list(
            self._session.scalars(
                select(HoldingLot)
                .where(
                    HoldingLot.symbol == normalized_symbol,
                    HoldingLot.remaining_quantity > 0,
                )
                .order_by(HoldingLot.acquired_at_utc, HoldingLot.id)
            )
        )
        held_quantity = sum(lot.remaining_quantity for lot in lots)
        if quantity > held_quantity:
            self._session.rollback()
            raise InsufficientHoldingsError(
                f"cannot sell {quantity}; only {held_quantity} held"
            )

        transaction = LedgerTransaction(
            transaction_type="sell",
            symbol=normalized_symbol,
            quantity=quantity,
            price_paise=price_paise,
            fees_paise=fees_paise,
            transacted_at_utc=self._as_utc(transacted_at),
            note=note,
            idempotency_key=normalized_key,
        )
        try:
            self._session.add(transaction)
            self._session.flush()
            remaining_to_sell = quantity
            allocated_fees = 0
            total_realized_pnl = 0

            for lot in lots:
                if remaining_to_sell == 0:
                    break
                matched_quantity = min(remaining_to_sell, lot.remaining_quantity)
                cost_basis = self._allocate_lot_cost(lot, matched_quantity)
                fee_share = self._allocate_sale_fees(
                    fees_paise,
                    matched_quantity,
                    quantity,
                    allocated_fees,
                    remaining_to_sell,
                )
                gross_proceeds = matched_quantity * price_paise
                net_proceeds = gross_proceeds - fee_share
                realized_pnl = net_proceeds - cost_basis

                realization = LotRealization(
                    sell_transaction_id=transaction.id,
                    holding_lot_id=lot.id,
                    quantity=matched_quantity,
                    cost_basis_paise=cost_basis,
                    gross_proceeds_paise=gross_proceeds,
                    sell_fees_paise=fee_share,
                    net_proceeds_paise=net_proceeds,
                    realized_pnl_paise=realized_pnl,
                    realized_at_utc=transaction.transacted_at_utc,
                )
                self._session.add(realization)
                lot.remaining_quantity -= matched_quantity
                lot.remaining_cost_paise -= cost_basis
                remaining_to_sell -= matched_quantity
                allocated_fees += fee_share
                total_realized_pnl += realized_pnl

            vault_credit = max(total_realized_pnl, 0)
            if vault_credit:
                self._session.add(
                    ProfitVaultEntry(
                        sell_transaction_id=transaction.id,
                        amount_paise=vault_credit,
                    )
                )
            self._session.flush()
            result = SellResult(transaction.id, total_realized_pnl, vault_credit)
            if commit:
                self._session.commit()
            return result
        except Exception:
            self._session.rollback()
            raise

    def get_summary(self) -> PortfolioSummary:
        """Derive book-cost balances entirely from immutable ledger records."""
        movements = list(self._session.scalars(select(CapitalMovement)))
        transactions = list(self._session.scalars(select(LedgerTransaction)))
        lots = list(
            self._session.scalars(
                select(HoldingLot).where(HoldingLot.remaining_quantity > 0)
            )
        )
        realizations = list(self._session.scalars(select(LotRealization)))
        vault_entries = list(self._session.scalars(select(ProfitVaultEntry)))

        cash_balance = sum(
            movement.amount_paise
            if movement.movement_type == "deposit"
            else -movement.amount_paise
            for movement in movements
        )
        for transaction in transactions:
            gross = transaction.quantity * transaction.price_paise
            if transaction.transaction_type == "buy":
                cash_balance -= gross + transaction.fees_paise
            else:
                cash_balance += gross - transaction.fees_paise

        vault_balance = sum(entry.amount_paise for entry in vault_entries)
        open_cost_basis = sum(lot.remaining_cost_paise for lot in lots)
        realized_pnl = sum(item.realized_pnl_paise for item in realizations)
        holdings_by_symbol: dict[str, tuple[int, int]] = {}
        for lot in lots:
            held_quantity, held_cost = holdings_by_symbol.get(lot.symbol, (0, 0))
            holdings_by_symbol[lot.symbol] = (
                held_quantity + lot.remaining_quantity,
                held_cost + lot.remaining_cost_paise,
            )
        holdings = tuple(
            HoldingSummary(symbol, quantity, cost)
            for symbol, (quantity, cost) in sorted(holdings_by_symbol.items())
        )
        deployable_cash = cash_balance - vault_balance
        return PortfolioSummary(
            cash_balance_paise=cash_balance,
            deployable_cash_paise=deployable_cash,
            open_cost_basis_paise=open_cost_basis,
            realized_pnl_paise=realized_pnl,
            profit_vault_balance_paise=vault_balance,
            risk_capital_paise=deployable_cash + open_cost_basis,
            holdings=holdings,
        )

    def _record_capital_movement(
        self,
        movement_type: str,
        amount_paise: int,
        occurred_at: datetime,
        note: str | None,
        idempotency_key: str | None,
        commit: bool = True,
    ) -> CapitalMovementResult:
        self._validate_positive_paise(amount_paise, "amount_paise")
        normalized_key = self._normalize_idempotency_key(idempotency_key)
        existing = self._find_capital_movement(normalized_key)
        if existing is not None:
            self._ensure_capital_movement_replay(
                existing, movement_type, amount_paise, occurred_at, note
            )
            return CapitalMovementResult(
                existing.id, self.get_summary().cash_balance_paise
            )
        cash_before = self.get_summary().cash_balance_paise
        if movement_type == "withdrawal" and amount_paise > cash_before:
            self._session.rollback()
            raise InsufficientCashError("withdrawal exceeds cash balance")
        movement = CapitalMovement(
            movement_type=movement_type,
            amount_paise=amount_paise,
            occurred_at_utc=self._as_utc(occurred_at),
            note=note,
            idempotency_key=normalized_key,
        )
        try:
            self._session.add(movement)
            self._session.flush()
            movement_id = movement.id
            if commit:
                self._session.commit()
            cash_change = amount_paise if movement_type == "deposit" else -amount_paise
            return CapitalMovementResult(
                movement_id, cash_before + cash_change
            )
        except Exception:
            self._session.rollback()
            raise

    def _find_capital_movement(
        self, idempotency_key: str | None
    ) -> CapitalMovement | None:
        if idempotency_key is None:
            return None
        return self._session.scalar(
            select(CapitalMovement).where(
                CapitalMovement.idempotency_key == idempotency_key
            )
        )

    def _find_transaction(
        self, idempotency_key: str | None
    ) -> LedgerTransaction | None:
        if idempotency_key is None:
            return None
        return self._session.scalar(
            select(LedgerTransaction).where(
                LedgerTransaction.idempotency_key == idempotency_key
            )
        )

    def _ensure_capital_movement_replay(
        self,
        existing: CapitalMovement,
        movement_type: str,
        amount_paise: int,
        occurred_at: datetime,
        note: str | None,
    ) -> None:
        if (
            existing.movement_type != movement_type
            or existing.amount_paise != amount_paise
            or self._persisted_as_utc(existing.occurred_at_utc)
            != self._as_utc(occurred_at)
            or existing.note != note
        ):
            raise IdempotencyConflictError(
                "idempotency key was already used for a different capital movement"
            )

    def _ensure_transaction_replay(
        self,
        existing: LedgerTransaction,
        transaction_type: str,
        symbol: str,
        quantity: int,
        price_paise: int,
        fees_paise: int,
        transacted_at: datetime,
        note: str | None,
    ) -> None:
        if (
            existing.transaction_type != transaction_type
            or existing.symbol != symbol
            or existing.quantity != quantity
            or existing.price_paise != price_paise
            or existing.fees_paise != fees_paise
            or self._persisted_as_utc(existing.transacted_at_utc)
            != self._as_utc(transacted_at)
            or existing.note != note
        ):
            raise IdempotencyConflictError(
                "idempotency key was already used for a different transaction"
            )

    @staticmethod
    def _allocate_lot_cost(lot: HoldingLot, matched_quantity: int) -> int:
        if matched_quantity == lot.remaining_quantity:
            return lot.remaining_cost_paise
        return lot.remaining_cost_paise * matched_quantity // lot.remaining_quantity

    @staticmethod
    def _allocate_sale_fees(
        total_fees: int,
        matched_quantity: int,
        total_quantity: int,
        already_allocated: int,
        remaining_to_sell: int,
    ) -> int:
        if matched_quantity == remaining_to_sell:
            return total_fees - already_allocated
        return total_fees * matched_quantity // total_quantity

    @classmethod
    def _validate_trade_input(
        cls,
        symbol: str,
        quantity: int,
        price_paise: int,
        fees_paise: int,
        transacted_at: datetime,
    ) -> str:
        normalized_symbol = symbol.strip().upper()
        if not normalized_symbol or len(normalized_symbol) > 20:
            raise LedgerValidationError("symbol must contain 1 to 20 characters")
        if quantity <= 0:
            raise LedgerValidationError("quantity must be positive")
        cls._validate_positive_paise(price_paise, "price_paise")
        if fees_paise < 0:
            raise LedgerValidationError("fees_paise cannot be negative")
        cls._as_utc(transacted_at)
        return normalized_symbol

    @staticmethod
    def _validate_positive_paise(value: int, field: str) -> None:
        if value <= 0:
            raise LedgerValidationError(f"{field} must be positive")

    @staticmethod
    def _normalize_idempotency_key(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or len(normalized) > 128:
            raise LedgerValidationError(
                "idempotency key must contain 1 to 128 characters"
            )
        return normalized

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise LedgerValidationError("timestamps must include a timezone")
        return value.astimezone(UTC)

    @staticmethod
    def _persisted_as_utc(value: datetime) -> datetime:
        """Restore UTC metadata omitted by SQLite's datetime adapter."""
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
