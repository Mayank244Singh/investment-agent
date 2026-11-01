"""Portfolio ledger API request and response schemas."""

import re
from datetime import UTC, datetime
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, Field, field_validator

PositivePaise = Annotated[int, Field(strict=True, gt=0)]
NonnegativePaise = Annotated[int, Field(strict=True, ge=0)]
PositiveQuantity = Annotated[int, Field(strict=True, gt=0)]


def _reject_future_timestamp(value: datetime) -> datetime:
    if value.astimezone(UTC) > datetime.now(UTC):
        raise ValueError("ledger event date cannot be in the future")
    return value


class CapitalMovementCreate(BaseModel):
    """A manually entered historical deposit or withdrawal."""

    amount_paise: PositivePaise
    occurred_at_utc: AwareDatetime
    note: Annotated[str | None, Field(max_length=2_000)] = None

    _validate_date = field_validator("occurred_at_utc")(_reject_future_timestamp)


class TradeCreate(BaseModel):
    """A manually entered historical buy or sell."""

    symbol: Annotated[str, Field(min_length=1, max_length=20)]
    quantity: PositiveQuantity
    price_paise: PositivePaise
    fees_paise: NonnegativePaise = 0
    transacted_at_utc: AwareDatetime
    note: Annotated[str | None, Field(max_length=2_000)] = None

    @field_validator("symbol")
    @classmethod
    def validate_symbol(cls, value: str) -> str:
        """Normalize and validate a compact NSE-style cash-equity symbol."""
        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.&-]*", normalized):
            raise ValueError("symbol contains unsupported characters")
        return normalized

    _validate_date = field_validator("transacted_at_utc")(
        _reject_future_timestamp
    )


class LedgerSummaryResponse(BaseModel):
    """Book-cost portfolio totals without unrealised P&L."""

    cash_balance_paise: int
    deployable_cash_paise: int
    open_cost_basis_paise: int
    realized_pnl_paise: int
    profit_vault_balance_paise: int
    risk_capital_paise: int


class HoldingResponse(BaseModel):
    """An aggregate open holding at book cost."""

    symbol: str
    quantity: int
    cost_basis_paise: int


class CapitalMovementResponse(BaseModel):
    """An immutable capital deposit or withdrawal."""

    id: int
    movement_type: str
    amount_paise: int
    occurred_at_utc: datetime
    note: str | None
    idempotency_key: str | None


class LedgerTransactionResponse(BaseModel):
    """An immutable manually entered buy or sell."""

    id: int
    transaction_type: str
    symbol: str
    quantity: int
    price_paise: int
    fees_paise: int
    transacted_at_utc: datetime
    note: str | None
    idempotency_key: str | None


class HoldingLotResponse(BaseModel):
    """One FIFO lot and its remaining cost basis."""

    id: int
    buy_transaction_id: int
    symbol: str
    acquired_at_utc: datetime
    original_quantity: int
    remaining_quantity: int
    total_cost_paise: int
    remaining_cost_paise: int


class LotRealizationResponse(BaseModel):
    """One realised portion of a FIFO lot."""

    id: int
    sell_transaction_id: int
    holding_lot_id: int
    quantity: int
    cost_basis_paise: int
    gross_proceeds_paise: int
    sell_fees_paise: int
    net_proceeds_paise: int
    realized_pnl_paise: int
    realized_at_utc: datetime


class ProfitVaultEntryResponse(BaseModel):
    """A positive realised profit reserved in the vault."""

    id: int
    sell_transaction_id: int
    amount_paise: int
    created_at_utc: datetime


class LedgerResponse(BaseModel):
    """Complete readable append-only ledger state."""

    summary: LedgerSummaryResponse
    holdings: list[HoldingResponse]
    capital_movements: list[CapitalMovementResponse]
    transactions: list[LedgerTransactionResponse]
    lots: list[HoldingLotResponse]
    realizations: list[LotRealizationResponse]
    profit_vault_entries: list[ProfitVaultEntryResponse]
