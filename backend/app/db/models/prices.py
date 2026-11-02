"""Locally imported, unadjusted end-of-day equity prices."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Instrument(Base):
    """An exchange instrument identified by its normalized symbol and series."""

    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("symbol", "series", name="uq_instruments_symbol_series"),
        CheckConstraint("length(symbol) BETWEEN 1 AND 20", name="valid_symbol_length"),
        CheckConstraint("symbol = trim(symbol)", name="trimmed_symbol"),
        CheckConstraint("symbol = upper(symbol)", name="uppercase_symbol"),
        CheckConstraint("series = 'EQ'", name="eq_series_only"),
        CheckConstraint(
            "isin IS NULL OR (length(isin) = 12 AND isin = upper(isin))",
            name="valid_isin",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    series: Mapped[str] = mapped_column(String(8), nullable=False)
    isin: Mapped[str | None] = mapped_column(String(12))


class ImportBatch(Base):
    """Provenance and outcome for one manually supplied local file."""

    __tablename__ = "import_batches"
    __table_args__ = (
        CheckConstraint("length(file_name) BETWEEN 1 AND 255", name="valid_file_name"),
        CheckConstraint("length(source_label) BETWEEN 1 AND 100", name="valid_source"),
        CheckConstraint("length(file_sha256) = 64", name="valid_sha256_length"),
        CheckConstraint(
            "status IN ('completed', 'completed_with_skips')", name="valid_status"
        ),
        CheckConstraint("total_rows >= 0", name="nonnegative_total_rows"),
        CheckConstraint("imported_rows >= 0", name="nonnegative_imported_rows"),
        CheckConstraint("skipped_rows >= 0", name="nonnegative_skipped_rows"),
        CheckConstraint("rejected_rows >= 0", name="nonnegative_rejected_rows"),
        CheckConstraint(
            "total_rows = imported_rows + skipped_rows + rejected_rows",
            name="row_counts_reconcile",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    source_label: Mapped[str] = mapped_column(String(100), nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    imported_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    skipped_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    imported_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )


class DailyPrice(Base):
    """An unadjusted end-of-day OHLC price, stored in integer paise."""

    __tablename__ = "daily_prices"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "trade_date", name="uq_daily_prices_instrument_date"
        ),
        CheckConstraint("open_paise > 0", name="positive_open"),
        CheckConstraint("high_paise > 0", name="positive_high"),
        CheckConstraint("low_paise > 0", name="positive_low"),
        CheckConstraint("close_paise > 0", name="positive_close"),
        CheckConstraint("previous_close_paise > 0", name="positive_previous_close"),
        CheckConstraint("high_paise >= low_paise", name="valid_range"),
        CheckConstraint(
            "open_paise BETWEEN low_paise AND high_paise", name="open_in_range"
        ),
        CheckConstraint(
            "close_paise BETWEEN low_paise AND high_paise", name="close_in_range"
        ),
        CheckConstraint("volume >= 0", name="nonnegative_volume"),
        CheckConstraint("price_basis = 'unadjusted'", name="unadjusted_only"),
        Index("ix_daily_prices_trade_date", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False
    )
    import_batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batches.id", ondelete="RESTRICT"), nullable=False
    )
    trade_date: Mapped[date] = mapped_column(Date, nullable=False)
    open_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    high_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    low_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    close_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_close_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    price_basis: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'unadjusted'")
    )
