"""Append-only portfolio ledger routes."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.portfolio import (
    CapitalMovementCreate,
    CapitalMovementResponse,
    LedgerResponse,
    LedgerTransactionResponse,
    TradeCreate,
)
from app.schemas.portfolio_reports import RealizedPnlReportResponse
from app.services.portfolio_report_service import (
    export_fifo_matches_csv,
    export_ledger_csv,
    read_realized_pnl_report,
)
from app.services.portfolio_service import (
    read_ledger,
    record_buy,
    record_deposit,
    record_sell,
    record_withdrawal,
)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])
SessionDependency = Annotated[Session, Depends(get_db_session)]
IdempotencyKeyHeader = Annotated[
    str | None,
    Header(alias="Idempotency-Key", min_length=1, max_length=128),
]


@router.get("/ledger", response_model=LedgerResponse)
def get_ledger(session: SessionDependency) -> LedgerResponse:
    """Return ledger records and derived book-cost balances."""
    return read_ledger(session)


@router.get("/realized-pnl", response_model=RealizedPnlReportResponse)
def get_realized_pnl(
    session: SessionDependency,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    financial_year: Annotated[int | None, Query(ge=1900, le=9998)] = None,
) -> RealizedPnlReportResponse:
    return read_realized_pnl_report(session, from_date, to_date, financial_year)


@router.get("/ledger.csv")
def get_ledger_csv(session: SessionDependency) -> Response:
    return Response(
        content=export_ledger_csv(session),
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="ledger.csv"',
        },
    )


@router.get("/fifo-matches.csv")
def get_fifo_matches_csv(
    session: SessionDependency,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    financial_year: Annotated[int | None, Query(ge=1900, le=9998)] = None,
) -> Response:
    return Response(
        content=export_fifo_matches_csv(session, from_date, to_date, financial_year),
        media_type="text/csv; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="fifo-matches.csv"',
        },
    )


@router.post(
    "/deposits",
    response_model=CapitalMovementResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_deposit(
    request: CapitalMovementCreate,
    session: SessionDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> CapitalMovementResponse:
    """Append a manually entered historical deposit."""
    return record_deposit(session, request, idempotency_key)


@router.post(
    "/withdrawals",
    response_model=CapitalMovementResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_withdrawal(
    request: CapitalMovementCreate,
    session: SessionDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> CapitalMovementResponse:
    """Append a manually entered historical withdrawal."""
    return record_withdrawal(session, request, idempotency_key)


@router.post(
    "/buys",
    response_model=LedgerTransactionResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_buy(
    request: TradeCreate,
    session: SessionDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> LedgerTransactionResponse:
    """Append a manually entered historical buy."""
    return record_buy(session, request, idempotency_key)


@router.post(
    "/sells",
    response_model=LedgerTransactionResponse,
    status_code=status.HTTP_201_CREATED,
)
def post_sell(
    request: TradeCreate,
    session: SessionDependency,
    idempotency_key: IdempotencyKeyHeader = None,
) -> LedgerTransactionResponse:
    """Append a manually entered historical sell."""
    return record_sell(session, request, idempotency_key)
