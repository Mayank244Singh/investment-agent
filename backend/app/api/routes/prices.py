"""Authenticated, read-only end-of-day price endpoints."""

from datetime import date
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import get_db_session
from app.schemas.prices import PortfolioValuationResponse, PriceCoverageResponse
from app.services.price_read_service import (
    read_portfolio_valuation,
    read_price_coverage,
)

router = APIRouter(tags=["prices"])
SessionDependency = Annotated[Session, Depends(get_db_session)]


@router.get("/portfolio/valuation", response_model=PortfolioValuationResponse)
def get_portfolio_valuation(
    request: Request,
    session: SessionDependency,
    as_of: date | None = None,
) -> PortfolioValuationResponse:
    """Value holdings with the latest usable EQ close on or before as-of."""
    settings = cast(Settings, request.app.state.settings)
    return read_portfolio_valuation(session, settings.price_staleness_days, as_of)


@router.get("/prices/coverage", response_model=PriceCoverageResponse)
def get_price_coverage(session: SessionDependency) -> PriceCoverageResponse:
    """Report latest EQ dates and import-file provenance."""
    return read_price_coverage(session)
