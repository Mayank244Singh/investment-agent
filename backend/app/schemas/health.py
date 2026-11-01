"""Health endpoint response schema."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Application readiness information."""

    status: Literal["ok"]
    database: Literal["ok"]
