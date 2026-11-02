"""API route registration."""

from fastapi import APIRouter, Security

from app.api.routes.health import router as health_router
from app.api.routes.portfolio import router as portfolio_router
from app.api.routes.prices import router as prices_router
from app.api.routes.risk import router as risk_router
from app.core.auth import require_bearer_token

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)

protected_router = APIRouter(dependencies=[Security(require_bearer_token)])
protected_router.include_router(portfolio_router)
protected_router.include_router(prices_router)
protected_router.include_router(risk_router)
api_router.include_router(protected_router)
