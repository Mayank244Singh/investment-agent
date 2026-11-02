"""Single-user bearer-token authentication for protected API routes."""

import secrets
from typing import Annotated, cast

from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, is_loopback_host

bearer_scheme = HTTPBearer(auto_error=False)
BearerCredentials = Annotated[
    HTTPAuthorizationCredentials | None,
    Security(bearer_scheme),
]


def validate_auth_startup(settings: Settings) -> None:
    """Refuse unsafe or unconfigured API startup."""
    if settings.auth_disabled and not is_loopback_host(settings.backend_host):
        raise RuntimeError("AUTH_DISABLED requires a loopback BACKEND_HOST")
    if settings.auth_token is None and not settings.auth_disabled:
        raise RuntimeError(
            "AUTH_TOKEN is required unless AUTH_DISABLED=true for local development"
        )


def require_bearer_token(
    request: Request, credentials: BearerCredentials
) -> None:
    """Allow a protected request only when its bearer token matches."""
    settings = cast(Settings, request.app.state.settings)
    if settings.auth_disabled:
        return
    if credentials is None or settings.auth_token is None:
        raise _unauthorized()

    provided = credentials.credentials.encode("utf-8")
    configured = settings.auth_token.get_secret_value().encode("utf-8")
    if not secrets.compare_digest(provided, configured):
        raise _unauthorized()


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unauthorized",
        headers={"WWW-Authenticate": "Bearer"},
    )
