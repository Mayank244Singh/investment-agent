"""Validation-only risk routes."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.risk import (
    ProposedTradeRequest,
    RiskRulesResponse,
    RiskValidationResponse,
)
from app.services.risk_service import read_risk_rules, validate_proposed_trade

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/rules", response_model=RiskRulesResponse)
def get_risk_rules() -> RiskRulesResponse:
    """Return validated rule configuration for display only."""
    return read_risk_rules()


@router.post("/validate", response_model=RiskValidationResponse)
def validate_trade(
    request: ProposedTradeRequest,
    session: Session = Depends(get_db_session),  # noqa: B008
) -> RiskValidationResponse:
    """Validate a proposal without storing or executing an order."""
    return validate_proposed_trade(session, request)
