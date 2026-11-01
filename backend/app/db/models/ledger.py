"""Append-only portfolio ledger persistence models."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CapitalMovement(Base):
    """An immutable deposit or withdrawal of original capital."""

    __tablename__ = "capital_movements"
    __table_args__ = (
        CheckConstraint(
            "movement_type IN ('deposit', 'withdrawal')",
            name="valid_movement_type",
        ),
        CheckConstraint("amount_paise > 0", name="positive_amount"),
        Index("ix_capital_movements_occurred_at_utc", "occurred_at_utc"),
        Index(
            "ux_capital_movements_idempotency_key",
            "idempotency_key",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    movement_type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class LedgerTransaction(Base):
    """An immutable manually entered buy or sell transaction."""

    __tablename__ = "ledger_transactions"
    __table_args__ = (
        CheckConstraint(
            "transaction_type IN ('buy', 'sell')",
            name="valid_transaction_type",
        ),
        CheckConstraint("length(symbol) BETWEEN 1 AND 20", name="valid_symbol_length"),
        CheckConstraint("symbol = upper(symbol)", name="uppercase_symbol"),
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("price_paise > 0", name="positive_price"),
        CheckConstraint("fees_paise >= 0", name="nonnegative_fees"),
        Index("ix_ledger_transactions_symbol", "symbol"),
        Index("ix_ledger_transactions_transacted_at_utc", "transacted_at_utc"),
        Index(
            "ux_ledger_transactions_idempotency_key",
            "idempotency_key",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_type: Mapped[str] = mapped_column(String(8), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fees_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    transacted_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class HoldingLot(Base):
    """A FIFO buy lot with its remaining quantity and exact paise cost."""

    __tablename__ = "holding_lots"
    __table_args__ = (
        CheckConstraint("length(symbol) BETWEEN 1 AND 20", name="valid_symbol_length"),
        CheckConstraint("symbol = upper(symbol)", name="uppercase_symbol"),
        CheckConstraint("original_quantity > 0", name="positive_original_quantity"),
        CheckConstraint(
            "remaining_quantity >= 0", name="nonnegative_remaining_quantity"
        ),
        CheckConstraint(
            "remaining_quantity <= original_quantity",
            name="remaining_quantity_within_original",
        ),
        CheckConstraint("total_cost_paise > 0", name="positive_total_cost"),
        CheckConstraint("remaining_cost_paise >= 0", name="nonnegative_remaining_cost"),
        CheckConstraint(
            "remaining_cost_paise <= total_cost_paise",
            name="remaining_cost_within_total",
        ),
        Index("ix_holding_lots_symbol_acquired", "symbol", "acquired_at_utc"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    buy_transaction_id: Mapped[int] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    acquired_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    original_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    total_cost_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    remaining_cost_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)


class LotRealization(Base):
    """The exact portion of a FIFO lot consumed by one sell transaction."""

    __tablename__ = "lot_realizations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("cost_basis_paise > 0", name="positive_cost_basis"),
        CheckConstraint("gross_proceeds_paise > 0", name="positive_gross_proceeds"),
        CheckConstraint("sell_fees_paise >= 0", name="nonnegative_sell_fees"),
        CheckConstraint("net_proceeds_paise >= 0", name="nonnegative_net_proceeds"),
        CheckConstraint(
            "net_proceeds_paise = gross_proceeds_paise - sell_fees_paise",
            name="net_proceeds_matches_components",
        ),
        CheckConstraint(
            "realized_pnl_paise = net_proceeds_paise - cost_basis_paise",
            name="realized_pnl_matches_components",
        ),
        Index("ix_lot_realizations_sell_transaction", "sell_transaction_id"),
        Index("ix_lot_realizations_holding_lot", "holding_lot_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sell_transaction_id: Mapped[int] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), nullable=False
    )
    holding_lot_id: Mapped[int] = mapped_column(
        ForeignKey("holding_lots.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_basis_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gross_proceeds_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sell_fees_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    net_proceeds_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    realized_pnl_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    realized_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ProfitVaultEntry(Base):
    """A positive realised profit reserved from deployable cash."""

    __tablename__ = "profit_vault_entries"
    __table_args__ = (CheckConstraint("amount_paise > 0", name="positive_amount"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sell_transaction_id: Mapped[int] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
