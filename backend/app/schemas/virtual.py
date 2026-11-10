"""Strict virtual-order request contract."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class VirtualOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    side: Literal["buy", "sell"]
    symbol: str
    quantity: int = Field(strict=True, gt=0)
    stop_loss_paise: int | None = Field(default=None, strict=True, gt=0)
    order_type: Literal["market"] = "market"

    @field_validator("symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized or len(normalized) > 20:
            raise ValueError("symbol must contain 1 to 20 characters")
        return normalized
