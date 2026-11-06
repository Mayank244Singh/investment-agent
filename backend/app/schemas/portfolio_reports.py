"""Read-only FIFO realised P&L report shapes."""

from datetime import date, datetime

from pydantic import BaseModel


class RealizedMatchResponse(BaseModel):
    sell_transaction_id: int
    buy_transaction_id: int
    holding_lot_id: int
    symbol: str
    quantity: int
    acquired_at_utc: datetime
    realized_at_utc: datetime
    holding_period_days: int
    cost_basis_paise: int
    gross_proceeds_paise: int
    sell_fees_paise: int
    net_proceeds_paise: int
    realized_pnl_paise: int


class SymbolRealizedPnlResponse(BaseModel):
    symbol: str
    quantity_sold: int
    cost_basis_paise: int
    gross_proceeds_paise: int
    sell_fees_paise: int
    net_proceeds_paise: int
    realized_pnl_paise: int
    shortest_holding_period_days: int
    longest_holding_period_days: int


class RealizedPnlReportResponse(BaseModel):
    from_date: date | None
    to_date: date | None
    financial_year: int | None
    symbols: list[SymbolRealizedPnlResponse]
    matches: list[RealizedMatchResponse]
    total_realized_pnl_paise: int
