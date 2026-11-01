"""Create the append-only portfolio ledger tables.

Revision ID: 0001_initial_ledger
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_initial_ledger"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the initial portfolio-ledger schema."""
    op.create_table(
        "capital_movements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("movement_type", sa.String(length=16), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "movement_type IN ('deposit', 'withdrawal')",
            name=op.f("ck_capital_movements_valid_movement_type"),
        ),
        sa.CheckConstraint(
            "amount_paise > 0", name=op.f("ck_capital_movements_positive_amount")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capital_movements")),
    )
    op.create_index(
        "ix_capital_movements_occurred_at_utc",
        "capital_movements",
        ["occurred_at_utc"],
        unique=False,
    )

    op.create_table(
        "ledger_transactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("transaction_type", sa.String(length=8), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price_paise", sa.BigInteger(), nullable=False),
        sa.Column(
            "fees_paise", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("transacted_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "transaction_type IN ('buy', 'sell')",
            name=op.f("ck_ledger_transactions_valid_transaction_type"),
        ),
        sa.CheckConstraint(
            "length(symbol) BETWEEN 1 AND 20",
            name=op.f("ck_ledger_transactions_valid_symbol_length"),
        ),
        sa.CheckConstraint(
            "symbol = upper(symbol)",
            name=op.f("ck_ledger_transactions_uppercase_symbol"),
        ),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_ledger_transactions_positive_quantity")
        ),
        sa.CheckConstraint(
            "price_paise > 0", name=op.f("ck_ledger_transactions_positive_price")
        ),
        sa.CheckConstraint(
            "fees_paise >= 0",
            name=op.f("ck_ledger_transactions_nonnegative_fees"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_transactions")),
    )
    op.create_index(
        "ix_ledger_transactions_symbol",
        "ledger_transactions",
        ["symbol"],
        unique=False,
    )
    op.create_index(
        "ix_ledger_transactions_transacted_at_utc",
        "ledger_transactions",
        ["transacted_at_utc"],
        unique=False,
    )

    op.create_table(
        "holding_lots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("buy_transaction_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("acquired_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_quantity", sa.Integer(), nullable=False),
        sa.Column("remaining_quantity", sa.Integer(), nullable=False),
        sa.Column("total_cost_paise", sa.BigInteger(), nullable=False),
        sa.Column("remaining_cost_paise", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "length(symbol) BETWEEN 1 AND 20",
            name=op.f("ck_holding_lots_valid_symbol_length"),
        ),
        sa.CheckConstraint(
            "symbol = upper(symbol)",
            name=op.f("ck_holding_lots_uppercase_symbol"),
        ),
        sa.CheckConstraint(
            "original_quantity > 0",
            name=op.f("ck_holding_lots_positive_original_quantity"),
        ),
        sa.CheckConstraint(
            "remaining_quantity >= 0",
            name=op.f("ck_holding_lots_nonnegative_remaining_quantity"),
        ),
        sa.CheckConstraint(
            "remaining_quantity <= original_quantity",
            name=op.f("ck_holding_lots_remaining_quantity_within_original"),
        ),
        sa.CheckConstraint(
            "total_cost_paise > 0", name=op.f("ck_holding_lots_positive_total_cost")
        ),
        sa.CheckConstraint(
            "remaining_cost_paise >= 0",
            name=op.f("ck_holding_lots_nonnegative_remaining_cost"),
        ),
        sa.CheckConstraint(
            "remaining_cost_paise <= total_cost_paise",
            name=op.f("ck_holding_lots_remaining_cost_within_total"),
        ),
        sa.ForeignKeyConstraint(
            ["buy_transaction_id"],
            ["ledger_transactions.id"],
            name=op.f("fk_holding_lots_buy_transaction_id_ledger_transactions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holding_lots")),
        sa.UniqueConstraint(
            "buy_transaction_id",
            name=op.f("uq_holding_lots_buy_transaction_id"),
        ),
    )
    op.create_index(
        "ix_holding_lots_symbol_acquired",
        "holding_lots",
        ["symbol", "acquired_at_utc"],
        unique=False,
    )

    op.create_table(
        "lot_realizations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sell_transaction_id", sa.Integer(), nullable=False),
        sa.Column("holding_lot_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("cost_basis_paise", sa.BigInteger(), nullable=False),
        sa.Column("gross_proceeds_paise", sa.BigInteger(), nullable=False),
        sa.Column("sell_fees_paise", sa.BigInteger(), nullable=False),
        sa.Column("net_proceeds_paise", sa.BigInteger(), nullable=False),
        sa.Column("realized_pnl_paise", sa.BigInteger(), nullable=False),
        sa.Column("realized_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "quantity > 0", name=op.f("ck_lot_realizations_positive_quantity")
        ),
        sa.CheckConstraint(
            "cost_basis_paise > 0",
            name=op.f("ck_lot_realizations_positive_cost_basis"),
        ),
        sa.CheckConstraint(
            "gross_proceeds_paise > 0",
            name=op.f("ck_lot_realizations_positive_gross_proceeds"),
        ),
        sa.CheckConstraint(
            "sell_fees_paise >= 0",
            name=op.f("ck_lot_realizations_nonnegative_sell_fees"),
        ),
        sa.CheckConstraint(
            "net_proceeds_paise >= 0",
            name=op.f("ck_lot_realizations_nonnegative_net_proceeds"),
        ),
        sa.CheckConstraint(
            "net_proceeds_paise = gross_proceeds_paise - sell_fees_paise",
            name=op.f("ck_lot_realizations_net_proceeds_matches_components"),
        ),
        sa.CheckConstraint(
            "realized_pnl_paise = net_proceeds_paise - cost_basis_paise",
            name=op.f("ck_lot_realizations_realized_pnl_matches_components"),
        ),
        sa.ForeignKeyConstraint(
            ["holding_lot_id"],
            ["holding_lots.id"],
            name=op.f("fk_lot_realizations_holding_lot_id_holding_lots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sell_transaction_id"],
            ["ledger_transactions.id"],
            name=op.f("fk_lot_realizations_sell_transaction_id_ledger_transactions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lot_realizations")),
    )
    op.create_index(
        "ix_lot_realizations_holding_lot",
        "lot_realizations",
        ["holding_lot_id"],
        unique=False,
    )
    op.create_index(
        "ix_lot_realizations_sell_transaction",
        "lot_realizations",
        ["sell_transaction_id"],
        unique=False,
    )

    op.create_table(
        "profit_vault_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sell_transaction_id", sa.Integer(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount_paise > 0", name=op.f("ck_profit_vault_entries_positive_amount")
        ),
        sa.ForeignKeyConstraint(
            ["sell_transaction_id"],
            ["ledger_transactions.id"],
            name=op.f(
                "fk_profit_vault_entries_sell_transaction_id_ledger_transactions"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profit_vault_entries")),
        sa.UniqueConstraint(
            "sell_transaction_id",
            name=op.f("uq_profit_vault_entries_sell_transaction_id"),
        ),
    )


def downgrade() -> None:
    """Remove the initial portfolio-ledger schema."""
    op.drop_table("profit_vault_entries")
    op.drop_index("ix_lot_realizations_sell_transaction", table_name="lot_realizations")
    op.drop_index("ix_lot_realizations_holding_lot", table_name="lot_realizations")
    op.drop_table("lot_realizations")
    op.drop_index("ix_holding_lots_symbol_acquired", table_name="holding_lots")
    op.drop_table("holding_lots")
    op.drop_index(
        "ix_ledger_transactions_transacted_at_utc",
        table_name="ledger_transactions",
    )
    op.drop_index("ix_ledger_transactions_symbol", table_name="ledger_transactions")
    op.drop_table("ledger_transactions")
    op.drop_index(
        "ix_capital_movements_occurred_at_utc", table_name="capital_movements"
    )
    op.drop_table("capital_movements")
