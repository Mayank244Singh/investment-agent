"""Authenticated synthetic virtual-account routes."""

from collections.abc import Generator
from typing import Annotated, cast

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.schemas.virtual import VirtualOrderRequest
from app.services.virtual_trading_service import (
    VirtualIdempotencyConflict,
    VirtualOrder,
    VirtualUnavailable,
    account_summary,
    list_quotes,
    order_history,
    preview_order,
    submit_order,
    virtual_session,
)

router = APIRouter(prefix="/virtual", tags=["virtual synthetic trading"])


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _session(request: Request) -> Generator[Session, None, None]:
    try:
        session = virtual_session(_settings(request))
    except VirtualUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    try:
        yield session
    finally:
        session.close()


VirtualSession = Annotated[Session, Depends(_session)]


@router.get("/account")
def get_account(request: Request, session: VirtualSession) -> dict[str, object]:
    return account_summary(session, _settings(request))


@router.get("/quotes")
def get_quotes(session: VirtualSession) -> list[dict[str, object]]:
    del session
    return list_quotes()


@router.post("/orders/preview")
def post_preview(
    order: VirtualOrderRequest,
    request: Request,
    session: VirtualSession,
) -> dict[str, object]:
    return preview_order(
        session, VirtualOrder(**order.model_dump()), _settings(request)
    )


@router.post("/orders")
def post_order(
    order: VirtualOrderRequest,
    request: Request,
    session: VirtualSession,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=1, max_length=128)
    ],
) -> dict[str, object]:
    try:
        return submit_order(
            session,
            VirtualOrder(**order.model_dump()),
            idempotency_key,
            _settings(request),
        )
    except VirtualIdempotencyConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/orders")
def get_orders(session: VirtualSession) -> list[dict[str, object]]:
    return order_history(session)
