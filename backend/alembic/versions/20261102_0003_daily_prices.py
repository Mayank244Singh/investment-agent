"""Add instruments, import provenance, and unadjusted daily prices.

Revision ID: 0003_daily_prices
Revises: 0002_ledger_idempotency
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_daily_prices"
down_revision: str | None = "0002_ledger_idempotency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the three tables for local end-of-day price storage."""
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=20), nullable=False),
        sa.Column("series", sa.String(length=8), nullable=False),
        sa.Column("isin", sa.String(length=12), nullable=True),
        sa.CheckConstraint(
            "length(symbol) BETWEEN 1 AND 20",
            name=op.f("ck_instruments_valid_symbol_length"),
        ),
        sa.CheckConstraint(
            "symbol = trim(symbol)", name=op.f("ck_instruments_trimmed_symbol")
        ),
        sa.CheckConstraint(
            "symbol = upper(symbol)", name=op.f("ck_instruments_uppercase_symbol")
        ),
        sa.CheckConstraint("series = 'EQ'", name=op.f("ck_instruments_eq_series_only")),
        sa.CheckConstraint(
            "isin IS NULL OR (length(isin) = 12 AND isin = upper(isin))",
            name=op.f("ck_instruments_valid_isin"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
        sa.UniqueConstraint("symbol", "series", name="uq_instruments_symbol_series"),
    )
    op.create_table(
        "import_batches",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_label", sa.String(length=100), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("imported_rows", sa.Integer(), nullable=False),
        sa.Column("skipped_rows", sa.Integer(), nullable=False),
        sa.Column("rejected_rows", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "imported_at_utc",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(file_name) BETWEEN 1 AND 255",
            name=op.f("ck_import_batches_valid_file_name"),
        ),
        sa.CheckConstraint(
            "length(source_label) BETWEEN 1 AND 100",
            name=op.f("ck_import_batches_valid_source"),
        ),
        sa.CheckConstraint(
            "length(file_sha256) = 64",
            name=op.f("ck_import_batches_valid_sha256_length"),
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'completed_with_skips')",
            name=op.f("ck_import_batches_valid_status"),
        ),
        sa.CheckConstraint(
            "total_rows >= 0", name=op.f("ck_import_batches_nonnegative_total_rows")
        ),
        sa.CheckConstraint(
            "imported_rows >= 0",
            name=op.f("ck_import_batches_nonnegative_imported_rows"),
        ),
        sa.CheckConstraint(
            "skipped_rows >= 0", name=op.f("ck_import_batches_nonnegative_skipped_rows")
        ),
        sa.CheckConstraint(
            "rejected_rows >= 0",
            name=op.f("ck_import_batches_nonnegative_rejected_rows"),
        ),
        sa.CheckConstraint(
            "total_rows = imported_rows + skipped_rows + rejected_rows",
            name=op.f("ck_import_batches_row_counts_reconcile"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_batches")),
        sa.UniqueConstraint("file_sha256", name=op.f("uq_import_batches_file_sha256")),
    )
    op.create_table(
        "daily_prices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("import_batch_id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open_paise", sa.BigInteger(), nullable=False),
        sa.Column("high_paise", sa.BigInteger(), nullable=False),
        sa.Column("low_paise", sa.BigInteger(), nullable=False),
        sa.Column("close_paise", sa.BigInteger(), nullable=False),
        sa.Column("previous_close_paise", sa.BigInteger(), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.Column(
            "price_basis",
            sa.String(length=10),
            server_default=sa.text("'unadjusted'"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "open_paise > 0", name=op.f("ck_daily_prices_positive_open")
        ),
        sa.CheckConstraint(
            "high_paise > 0", name=op.f("ck_daily_prices_positive_high")
        ),
        sa.CheckConstraint("low_paise > 0", name=op.f("ck_daily_prices_positive_low")),
        sa.CheckConstraint(
            "close_paise > 0", name=op.f("ck_daily_prices_positive_close")
        ),
        sa.CheckConstraint(
            "previous_close_paise > 0",
            name=op.f("ck_daily_prices_positive_previous_close"),
        ),
        sa.CheckConstraint(
            "high_paise >= low_paise", name=op.f("ck_daily_prices_valid_range")
        ),
        sa.CheckConstraint(
            "open_paise BETWEEN low_paise AND high_paise",
            name=op.f("ck_daily_prices_open_in_range"),
        ),
        sa.CheckConstraint(
            "close_paise BETWEEN low_paise AND high_paise",
            name=op.f("ck_daily_prices_close_in_range"),
        ),
        sa.CheckConstraint(
            "volume >= 0", name=op.f("ck_daily_prices_nonnegative_volume")
        ),
        sa.CheckConstraint(
            "price_basis = 'unadjusted'", name=op.f("ck_daily_prices_unadjusted_only")
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_daily_prices_instrument_id_instruments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["import_batch_id"],
            ["import_batches.id"],
            name=op.f("fk_daily_prices_import_batch_id_import_batches"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_daily_prices")),
        sa.UniqueConstraint(
            "instrument_id", "trade_date", name="uq_daily_prices_instrument_date"
        ),
    )
    op.create_index("ix_daily_prices_trade_date", "daily_prices", ["trade_date"])


def downgrade() -> None:
    """Remove price storage without changing the existing ledger."""
    op.drop_index("ix_daily_prices_trade_date", table_name="daily_prices")
    op.drop_table("daily_prices")
    op.drop_table("import_batches")
    op.drop_table("instruments")
