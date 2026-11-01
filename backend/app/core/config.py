"""Environment-based application settings."""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = PROJECT_ROOT / "data" / "investment_agent.db"
RiskPercent = Annotated[Decimal, Field(gt=0, le=100)]


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    app_name: str = "Investment Agent"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = f"sqlite:///{DEFAULT_DATABASE_PATH}"
    risk_max_order_percent: RiskPercent = Decimal("10")
    risk_max_stock_allocation_percent: RiskPercent = Decimal("20")
    risk_max_loss_per_trade_percent: RiskPercent = Decimal("1")
    risk_max_daily_loss_percent: RiskPercent = Decimal("2")
    risk_daily_loss_timezone: str = "Asia/Kolkata"

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("risk_daily_loss_timezone")
    @classmethod
    def validate_risk_timezone(cls, value: str) -> str:
        """Reject unknown timezones during application startup."""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("risk_daily_loss_timezone must be valid") from exc
        return value


@lru_cache
def get_settings() -> Settings:
    """Return cached, validated application settings."""
    return Settings()
