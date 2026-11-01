"""Central HTTP exception handlers."""

import logging
from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.modules.portfolio import (
    IdempotencyConflictError,
    InsufficientCashError,
    InsufficientHoldingsError,
    LedgerError,
    LedgerValidationError,
)

logger = logging.getLogger(__name__)


def _error_payload(request: Request, detail: Any) -> dict[str, Any]:
    return {
        "detail": detail,
        "request_id": getattr(request.state, "request_id", None),
    }


def _response_headers(
    request: Request, headers: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Preserve supplied headers and always return the request ID."""
    response_headers = dict(headers) if headers else {}
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        response_headers["X-Request-ID"] = request_id
    return response_headers


def register_exception_handlers(app: FastAPI) -> None:
    """Register consistent JSON error responses for the API."""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(
        request: Request, exc: HTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(request, exc.detail),
            headers=_response_headers(request, exc.headers),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_payload(request, jsonable_encoder(exc.errors())),
            headers=_response_headers(request),
        )

    @app.exception_handler(LedgerError)
    async def ledger_exception_handler(
        request: Request, exc: LedgerError
    ) -> JSONResponse:
        status_code = 400
        if isinstance(
            exc,
            (
                IdempotencyConflictError,
                InsufficientCashError,
                InsufficientHoldingsError,
            ),
        ):
            status_code = 409
        elif isinstance(exc, LedgerValidationError):
            status_code = 422
        return JSONResponse(
            status_code=status_code,
            content=_error_payload(request, str(exc)),
            headers=_response_headers(request),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception(
            "Unhandled application exception",
            extra={"request_id": getattr(request.state, "request_id", None)},
        )
        return JSONResponse(
            status_code=500,
            content=_error_payload(request, "Internal server error"),
            headers=_response_headers(request),
        )
