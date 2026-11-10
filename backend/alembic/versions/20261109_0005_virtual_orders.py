"""Add isolated virtual-account marker and virtual order audit.

Revision ID: 0005_virtual_orders
Revises: 0004_market_data_foundation
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_virtual_orders"
down_revision: str | None = "0004_market_data_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "virtual_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("initial_capital_paise", sa.BigInteger(), nullable=False),
        sa.Column("fixture_sha256", sa.String(64), nullable=False),
        sa.Column("seeded_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_virtual_accounts_single_account"),
        sa.CheckConstraint("currency = 'INR'", name="ck_virtual_accounts_inr_only"),
        sa.CheckConstraint(
            "initial_capital_paise = 1000000",
            name="ck_virtual_accounts_opening_capital",
        ),
    )
    op.create_table(
        "virtual_order_attempts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("idempotency_key", sa.String(128), nullable=False, unique=True),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("attempted_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("stop_loss_paise", sa.BigInteger()),
        sa.Column("quote_date", sa.Date()),
        sa.Column("quote_sha256", sa.String(64)),
        sa.Column("reference_paise", sa.BigInteger()),
        sa.Column("fill_paise", sa.BigInteger()),
        sa.Column("fee_paise", sa.BigInteger(), nullable=False),
        sa.Column("charges_json", sa.Text()),
        sa.Column("planned_loss_paise", sa.BigInteger()),
        sa.Column("rejection_codes_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(8), nullable=False),
        sa.Column("ledger_transaction_id", sa.Integer(), unique=True),
        sa.ForeignKeyConstraint(
            ["ledger_transaction_id"], ["ledger_transactions.id"], ondelete="RESTRICT"
        ),
        sa.CheckConstraint(
            "status IN ('filled', 'rejected')",
            name="ck_virtual_order_attempts_valid_status",
        ),
        sa.CheckConstraint(
            "quantity > 0", name="ck_virtual_order_attempts_positive_quantity"
        ),
        sa.CheckConstraint(
            "fee_paise >= 0", name="ck_virtual_order_attempts_nonnegative_fee"
        ),
    )


def downgrade() -> None:
    op.drop_table("virtual_order_attempts")
    op.drop_table("virtual_accounts")
