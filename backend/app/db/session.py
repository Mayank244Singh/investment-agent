"""SQLAlchemy engine and session lifecycle."""

from collections.abc import Generator
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings


def _ensure_sqlite_directory(database_url: str) -> None:
    if database_url.startswith("sqlite:///"):
        Path(database_url.removeprefix("sqlite:///")).parent.mkdir(
            parents=True, exist_ok=True
        )


def create_database_engine() -> Engine:
    """Create the configured SQLAlchemy engine."""
    database_url = get_settings().database_url
    _ensure_sqlite_directory(database_url)
    database_engine = create_engine(
        database_url,
        connect_args=(
            {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        ),
        pool_pre_ping=True,
    )
    if database_url.startswith("sqlite"):

        @event.listens_for(database_engine, "connect")
        def enable_sqlite_foreign_keys(
            dbapi_connection: Any, _: Any
        ) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return database_engine


engine = create_database_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db_session() -> Generator[Session, None, None]:
    """Yield and reliably close one request-scoped database session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
