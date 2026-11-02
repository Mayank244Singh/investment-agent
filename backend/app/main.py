"""FastAPI application entry point."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.auth import validate_auth_startup
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging
from app.core.request_id import RequestIdMiddleware

logger = logging.getLogger(__name__)
FRONTEND_ORIGIN = "http://127.0.0.1:5173"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Create and configure the web application."""
    resolved_settings = settings or get_settings()
    validate_auth_startup(resolved_settings)
    configure_logging(resolved_settings.log_level)

    local_docs = resolved_settings.backend_host == "127.0.0.1"
    app = FastAPI(
        title=resolved_settings.app_name,
        docs_url="/docs" if local_docs else None,
        openapi_url="/openapi.json" if local_docs else None,
        redoc_url=None,
    )
    app.state.settings = resolved_settings
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[FRONTEND_ORIGIN],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Request-ID",
        ],
    )

    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)
    app.include_router(api_router)

    if resolved_settings.auth_disabled:
        logger.warning(
            "!!! AUTHENTICATION IS DISABLED; LOCAL DEVELOPMENT ONLY !!!"
        )
    if resolved_settings.backend_host != "127.0.0.1":
        logger.warning(
            "Backend host is not 127.0.0.1; the API may be network-accessible"
        )
    return app


app = create_app()
