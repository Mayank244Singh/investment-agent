"""Application health business logic."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.schemas.health import HealthResponse


def check_health(session: Session) -> HealthResponse:
    """Confirm that the configured database accepts a real query."""
    session.execute(text("SELECT 1"))
    return HealthResponse(status="ok", database="ok")
