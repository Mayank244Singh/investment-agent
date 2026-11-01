"""API route registration."""

from fastapi import APIRouter

from app.api.routes.health import router as health_router
from app.api.routes.portfolio import router as portfolio_router
from app.api.routes.risk import router as risk_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health_router)
api_router.include_router(portfolio_router)
api_router.include_router(risk_router)
