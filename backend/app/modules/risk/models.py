"""Data objects used by the independent risk validator."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.core.config import Settings


class RejectionCode(StrEnum):
    """Stable machine-readable reasons for rejecting a proposed trade."""

    MISSING_CONFIG = "missing_config"
    INVALID_CONFIG = "invalid_config"
    INVALID_SIDE = "invalid_side"
    INVALID_SYMBOL = "invalid_symbol"
    INVALID_QUANTITY = "invalid_quantity"
    INVALID_PRICE = "invalid_price"
    INVALID_FEES = "invalid_fees"
    INVALID_TIMESTAMP = "invalid_timestamp"
    MISSING_SNAPSHOT = "missing_snapshot"
    INVALID_SNAPSHOT = "invalid_snapshot"
    INSUFFICIENT_CASH = "insufficient_cash"
    MAX_ORDER_SIZE = "max_order_size"
    MAX_STOCK_ALLOCATION = "max_stock_allocation"
    STOP_LOSS_REQUIRED = "stop_loss_required"
    INVALID_STOP_LOSS = "invalid_stop_loss"
    MAX_LOSS_PER_TRADE = "max_loss_per_trade"
    MAX_DAILY_LOSS = "max_daily_loss"
    INSUFFICIENT_HOLDINGS = "insufficient_holdings"
    VALIDATION_ERROR = "validation_error"


@dataclass(frozen=True)
class ProposedTrade:
    """A proposal for validation only; it is not an executable order."""

    side: str | None
    symbol: str | None
    quantity: int | None
    price_paise: int | None
    proposed_at_utc: datetime | None
    stop_loss_paise: int | None = None
    fees_paise: int | None = 0


@dataclass(frozen=True)
class HoldingSnapshot:
    """One symbol's held quantity and book cost."""

    quantity: int
    cost_basis_paise: int


@dataclass(frozen=True)
class RealizedPnlEvent:
    """One persisted realised P&L event with a UTC timestamp."""

    realized_pnl_paise: int
    realized_at_utc: datetime


@dataclass(frozen=True)
class PortfolioSnapshot:
    """All portfolio facts required by risk validation."""

    cash_balance_paise: int | None
    profit_vault_balance_paise: int | None
    open_cost_basis_paise: int | None
    holdings: Mapping[str, HoldingSnapshot] | None
    realized_events: tuple[RealizedPnlEvent, ...] | None


@dataclass(frozen=True)
class RiskLimits:
    """Config-sourced limits; optional fields allow fail-closed validation."""

    max_order_percent: Decimal | None
    max_stock_allocation_percent: Decimal | None
    max_loss_per_trade_percent: Decimal | None
    max_daily_loss_percent: Decimal | None
    daily_loss_timezone: str | None

    @classmethod
    def from_settings(cls, settings: Settings) -> "RiskLimits":
        """Copy validated application configuration into the pure domain object."""
        return cls(
            max_order_percent=settings.risk_max_order_percent,
            max_stock_allocation_percent=(
                settings.risk_max_stock_allocation_percent
            ),
            max_loss_per_trade_percent=settings.risk_max_loss_per_trade_percent,
            max_daily_loss_percent=settings.risk_max_daily_loss_percent,
            daily_loss_timezone=settings.risk_daily_loss_timezone,
        )


@dataclass(frozen=True)
class RejectionReason:
    """One rule failure returned to the caller."""

    code: RejectionCode
    message: str


@dataclass(frozen=True)
class ValidationResult:
    """Complete validation outcome including every discovered failure."""

    approved: bool
    reasons: tuple[RejectionReason, ...]
