"""Virtual-only marker and audited order attempts; no second accounting ledger."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class VirtualAccount(Base):
    __tablename__ = "virtual_accounts"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_account"),
        CheckConstraint("currency = 'INR'", name="inr_only"),
        CheckConstraint("initial_capital_paise = 1000000", name="opening_capital"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    initial_capital_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fixture_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    seeded_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class VirtualOrderAttempt(Base):
    __tablename__ = "virtual_order_attempts"
    __table_args__ = (
        CheckConstraint("status IN ('filled', 'rejected')", name="valid_status"),
        CheckConstraint("quantity > 0", name="positive_quantity"),
        CheckConstraint("fee_paise >= 0", name="nonnegative_fee"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False
    )
    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    attempted_at_utc: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    stop_loss_paise: Mapped[int | None] = mapped_column(BigInteger)
    quote_date: Mapped[date | None] = mapped_column(Date)
    quote_sha256: Mapped[str | None] = mapped_column(String(64))
    reference_paise: Mapped[int | None] = mapped_column(BigInteger)
    fill_paise: Mapped[int | None] = mapped_column(BigInteger)
    fee_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    charges_json: Mapped[str | None] = mapped_column(Text)
    planned_loss_paise: Mapped[int | None] = mapped_column(BigInteger)
    rejection_codes_json: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    ledger_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), unique=True
    )
