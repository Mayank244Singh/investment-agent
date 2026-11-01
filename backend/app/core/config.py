"""Environment-based application settings."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "investment_agent.db"


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    app_name: str = "Investment Agent"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = f"sqlite:///{DEFAULT_DATABASE_PATH}"

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Return cached, validated application settings."""
    return Settings()
