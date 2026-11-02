"""Read-only price coverage and portfolio valuation API contracts."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

UnavailableReason = Literal["no_eq_price", "no_price_on_or_before_as_of", "stale_price"]


class HoldingValuationResponse(BaseModel):
    """One open FIFO holding, with an explicitly usable or unavailable price."""

    symbol: str
    quantity: int
    cost_basis_paise: int
    status: Literal["priced", "unavailable"]
    unavailable_reason: UnavailableReason | None
    last_price_date: date | None
    price_age_days: int | None
    close_paise: int | None
    market_value_paise: int | None
    unrealized_pnl_paise: int | None
    source_label: str | None
    file_name: str | None


class ValuationTotalsResponse(BaseModel):
    """Market totals cover priced holdings only; unpriced cost stays separate."""

    priced_holdings_count: int
    unpriced_holdings_count: int
    priced_cost_basis_paise: int
    priced_market_value_paise: int
    priced_unrealized_pnl_paise: int
    unpriced_cost_basis_paise: int
    total_open_cost_basis_paise: int


class PortfolioValuationResponse(BaseModel):
    """An as-of valuation that never assigns an estimated price."""

    as_of_date: date
    staleness_limit_days: int
    holdings: list[HoldingValuationResponse]
    totals: ValuationTotalsResponse


class SymbolPriceCoverageResponse(BaseModel):
    """Latest stored EQ price date for an instrument."""

    symbol: str
    series: Literal["EQ"] = "EQ"
    latest_price_date: date | None


class ImportBatchCoverageResponse(BaseModel):
    """Provenance and row counts for one completed local import."""

    id: int
    file_name: str
    source_label: str
    total_rows: int
    imported_rows: int
    skipped_rows: int
    rejected_rows: int
    status: str
    imported_at_utc: datetime


class PriceCoverageResponse(BaseModel):
    """Stored symbol coverage and local import provenance."""

    symbols: list[SymbolPriceCoverageResponse]
    import_batches: list[ImportBatchCoverageResponse]
