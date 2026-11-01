"""Validation-only risk API orchestration."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models.ledger import LotRealization
from app.modules.portfolio import LedgerService
from app.modules.risk import (
    HoldingSnapshot,
    PortfolioSnapshot,
    ProposedTrade,
    RealizedPnlEvent,
    RiskLimits,
    RiskValidator,
)
from app.schemas.risk import (
    ProposedTradeRequest,
    RejectionReasonResponse,
    RiskRulesResponse,
    RiskRuleStatusResponse,
    RiskValidationResponse,
)


def read_risk_rules() -> RiskRulesResponse:
    """Expose validated non-secret rule configuration to the dashboard."""
    settings = get_settings()
    return RiskRulesResponse(
        rules=[
            RiskRuleStatusResponse(
                code="sufficient_cash",
                label="Sufficient deployable cash",
                status="configured",
            ),
            RiskRuleStatusResponse(
                code="max_order_size",
                label="Maximum order size",
                status="configured",
                limit_percent=settings.risk_max_order_percent,
            ),
            RiskRuleStatusResponse(
                code="max_stock_allocation",
                label="Maximum single-stock allocation",
                status="configured",
                limit_percent=settings.risk_max_stock_allocation_percent,
            ),
            RiskRuleStatusResponse(
                code="max_loss_per_trade",
                label="Maximum loss per trade",
                status="configured",
                limit_percent=settings.risk_max_loss_per_trade_percent,
            ),
            RiskRuleStatusResponse(
                code="max_daily_loss",
                label="Maximum daily realised loss",
                status="configured",
                limit_percent=settings.risk_max_daily_loss_percent,
            ),
            RiskRuleStatusResponse(
                code="stop_loss_required",
                label="Stop-loss required for buys",
                status="configured",
            ),
            RiskRuleStatusResponse(
                code="quantity_price_sanity",
                label="Quantity and price sanity checks",
                status="configured",
            ),
            RiskRuleStatusResponse(
                code="no_short_selling",
                label="No short selling",
                status="configured",
            ),
        ],
        daily_loss_timezone=settings.risk_daily_loss_timezone,
    )


def validate_proposed_trade(
    session: Session, request: ProposedTradeRequest
) -> RiskValidationResponse:
    """Build a snapshot and validate without creating an order or ledger entry."""
    summary = LedgerService(session).get_summary()
    realizations = session.scalars(
        select(LotRealization).order_by(LotRealization.realized_at_utc)
    )
    snapshot = PortfolioSnapshot(
        cash_balance_paise=summary.cash_balance_paise,
        profit_vault_balance_paise=summary.profit_vault_balance_paise,
        open_cost_basis_paise=summary.open_cost_basis_paise,
        holdings={
            item.symbol: HoldingSnapshot(item.quantity, item.cost_basis_paise)
            for item in summary.holdings
        },
        realized_events=tuple(
            RealizedPnlEvent(
                item.realized_pnl_paise, _as_utc(item.realized_at_utc)
            )
            for item in realizations
        ),
    )
    trade = ProposedTrade(
        side=request.side,
        symbol=request.symbol,
        quantity=request.quantity,
        price_paise=request.price_paise,
        proposed_at_utc=request.proposed_at_utc,
        stop_loss_paise=request.stop_loss_paise,
        fees_paise=request.fees_paise,
    )
    result = RiskValidator(RiskLimits.from_settings(get_settings())).validate(
        trade, snapshot
    )
    return RiskValidationResponse(
        approved=result.approved,
        reasons=[
            RejectionReasonResponse(code=item.code, message=item.message)
            for item in result.reasons
        ],
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
