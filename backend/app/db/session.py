"""SQLAlchemy engine and session lifecycle."""

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
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
    return create_engine(
        database_url,
        connect_args=(
            {"check_same_thread": False} if database_url.startswith("sqlite") else {}
        ),
        pool_pre_ping=True,
    )


engine = create_database_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db_session() -> Generator[Session, None, None]:
    """Yield and reliably close one request-scoped database session."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
