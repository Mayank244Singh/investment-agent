"""Append-only portfolio ledger routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, status
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.portfolio import (
    CapitalMovementCreate,
    CapitalMovementResponse,
    LedgerResponse,
    LedgerTransactionResponse,
    TradeCreate,
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
