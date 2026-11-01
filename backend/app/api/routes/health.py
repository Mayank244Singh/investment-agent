"""Health endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db_session
from app.schemas.health import HealthResponse
from app.services.health_service import check_health

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def get_health(session: Session = Depends(get_db_session)) -> HealthResponse:  # noqa: B008
    """Report whether the application and its database are available."""
    return check_health(session)
