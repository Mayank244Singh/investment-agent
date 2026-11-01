"""Health endpoint tests."""

from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.db.session import get_db_session
from app.main import app


def test_health_returns_ok_when_database_is_available() -> None:
    """The health route confirms the application and SQLite database are ready."""
    with TestClient(app) as client:
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
    assert response.headers["x-request-id"]


def test_health_returns_internal_error_when_database_is_unavailable() -> None:
    """A database failure is handled by the central exception handler."""

    class UnavailableSession:
        def execute(self, _: object) -> None:
            raise SQLAlchemyError("database unavailable")

    def unavailable_session() -> Generator[UnavailableSession, None, None]:
        yield UnavailableSession()

    app.dependency_overrides[get_db_session] = unavailable_session
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/v1/health")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert response.json()["request_id"] == response.headers["x-request-id"]
