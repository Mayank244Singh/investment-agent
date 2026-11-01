"""Add optional idempotency keys to ledger events.

Revision ID: 0002_ledger_idempotency
Revises: 0001_initial_ledger
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_ledger_idempotency"
down_revision: str | None = "0001_initial_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Persist endpoint-scoped idempotency keys on immutable events."""
    op.add_column(
        "capital_movements",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ux_capital_movements_idempotency_key",
        "capital_movements",
        ["idempotency_key"],
        unique=True,
    )
    op.add_column(
        "ledger_transactions",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ux_ledger_transactions_idempotency_key",
        "ledger_transactions",
        ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    """Remove ledger-event idempotency keys."""
    op.drop_index(
        "ux_ledger_transactions_idempotency_key",
        table_name="ledger_transactions",
    )
    op.drop_column("ledger_transactions", "idempotency_key")
    op.drop_index(
        "ux_capital_movements_idempotency_key",
        table_name="capital_movements",
    )
    op.drop_column("capital_movements", "idempotency_key")
