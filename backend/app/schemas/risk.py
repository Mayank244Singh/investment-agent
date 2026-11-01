"""Risk-validation API request and response schemas."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from app.modules.risk.models import RejectionCode


class ProposedTradeRequest(BaseModel):
    """A validation-only proposal; it is never stored or executed as an order."""

    side: str | None = None
    symbol: str | None = None
    quantity: int | None = None
    price_paise: int | None = None
    proposed_at_utc: datetime | None = None
    stop_loss_paise: int | None = None
    fees_paise: int | None = 0


class RejectionReasonResponse(BaseModel):
    """One machine-readable risk rejection reason."""

    code: RejectionCode
    message: str


class RiskValidationResponse(BaseModel):
    """Approval or every reason for rejecting the proposal."""

    approved: bool
    reasons: list[RejectionReasonResponse]


class RiskRuleStatusResponse(BaseModel):
    """Public status for one configured validation rule."""

    code: str
    label: str
    status: Literal["configured"]
    limit_percent: Decimal | None = None


class RiskRulesResponse(BaseModel):
    """Current validated risk-rule configuration."""

    rules: list[RiskRuleStatusResponse]
    daily_loss_timezone: str
