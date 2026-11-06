"""Read-only FIFO reports and spreadsheet-safe ledger CSV exports."""

import csv
import io
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.ledger import (
    CapitalMovement,
    HoldingLot,
    LedgerTransaction,
    LotRealization,
    ProfitVaultEntry,
)
from app.modules.portfolio.errors import LedgerValidationError
from app.schemas.portfolio_reports import (
    RealizedMatchResponse,
    RealizedPnlReportResponse,
    SymbolRealizedPnlResponse,
)

MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")
LEDGER_COLUMNS = (
    "event_type",
    "record_id",
    "event_at_utc",
    "symbol",
    "quantity",
    "price_paise",
    "amount_paise",
    "fees_paise",
    "realized_pnl_paise",
    "profit_vault_credit_paise",
    "note",
    "idempotency_key",
)
MATCH_COLUMNS = (
    "sell_transaction_id",
    "buy_transaction_id",
    "holding_lot_id",
    "symbol",
    "quantity",
    "acquired_at_utc",
    "realized_at_utc",
    "holding_period_days",
    "cost_basis_paise",
    "gross_proceeds_paise",
    "sell_fees_paise",
    "net_proceeds_paise",
    "realized_pnl_paise",
)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _period(
    from_date: date | None, to_date: date | None, financial_year: int | None
) -> tuple[date | None, date | None]:
    if financial_year is not None:
        if from_date is not None or to_date is not None:
            raise LedgerValidationError(
                "financial_year cannot be combined with from/to"
            )
        return date(financial_year, 4, 1), date(financial_year + 1, 3, 31)
    if from_date is not None and to_date is not None and from_date > to_date:
        raise LedgerValidationError("from must be on or before to")
    return from_date, to_date


def _matches(
    session: Session, from_date: date | None, to_date: date | None
) -> list[RealizedMatchResponse]:
    rows = session.execute(
        select(LotRealization, LedgerTransaction, HoldingLot)
        .join(
            LedgerTransaction,
            LotRealization.sell_transaction_id == LedgerTransaction.id,
        )
        .join(HoldingLot, LotRealization.holding_lot_id == HoldingLot.id)
        .order_by(
            LotRealization.realized_at_utc,
            LotRealization.sell_transaction_id,
            LotRealization.id,
        )
    )
    matches: list[RealizedMatchResponse] = []
    for realization, sale, lot in rows:
        sold_at = _as_utc(realization.realized_at_utc)
        acquired_at = _as_utc(lot.acquired_at_utc)
        sold_day = sold_at.astimezone(MARKET_TIMEZONE).date()
        if from_date is not None and sold_day < from_date:
            continue
        if to_date is not None and sold_day > to_date:
            continue
        matches.append(
            RealizedMatchResponse(
                sell_transaction_id=sale.id,
                buy_transaction_id=lot.buy_transaction_id,
                holding_lot_id=lot.id,
                symbol=sale.symbol,
                quantity=realization.quantity,
                acquired_at_utc=acquired_at,
                realized_at_utc=sold_at,
                holding_period_days=(
                    sold_day - acquired_at.astimezone(MARKET_TIMEZONE).date()
                ).days,
                cost_basis_paise=realization.cost_basis_paise,
                gross_proceeds_paise=realization.gross_proceeds_paise,
                sell_fees_paise=realization.sell_fees_paise,
                net_proceeds_paise=realization.net_proceeds_paise,
                realized_pnl_paise=realization.realized_pnl_paise,
            )
        )
    return matches


def read_realized_pnl_report(
    session: Session,
    from_date: date | None = None,
    to_date: date | None = None,
    financial_year: int | None = None,
) -> RealizedPnlReportResponse:
    """Group immutable FIFO matches by symbol; fiscal dates use India time."""
    start, end = _period(from_date, to_date, financial_year)
    matches = _matches(session, start, end)
    by_symbol: dict[str, list[RealizedMatchResponse]] = {}
    for match in matches:
        by_symbol.setdefault(match.symbol, []).append(match)
    symbols = [
        SymbolRealizedPnlResponse(
            symbol=symbol,
            quantity_sold=sum(item.quantity for item in items),
            cost_basis_paise=sum(item.cost_basis_paise for item in items),
            gross_proceeds_paise=sum(item.gross_proceeds_paise for item in items),
            sell_fees_paise=sum(item.sell_fees_paise for item in items),
            net_proceeds_paise=sum(item.net_proceeds_paise for item in items),
            realized_pnl_paise=sum(item.realized_pnl_paise for item in items),
            shortest_holding_period_days=min(
                item.holding_period_days for item in items
            ),
            longest_holding_period_days=max(item.holding_period_days for item in items),
        )
        for symbol, items in sorted(by_symbol.items())
    ]
    return RealizedPnlReportResponse(
        from_date=start,
        to_date=end,
        financial_year=financial_year,
        symbols=symbols,
        matches=matches,
        total_realized_pnl_paise=sum(item.realized_pnl_paise for item in matches),
    )


def _safe_cell(value: object) -> str:
    """Prevent formula interpretation, including a signed negative paise value."""
    string = "" if value is None else str(value)
    return "'" + string if string.lstrip().startswith(("=", "+", "-", "@")) else string


def _csv_text(columns: tuple[str, ...], rows: list[tuple[object, ...]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_safe_cell(value) for value in row])
    return output.getvalue()


def export_ledger_csv(session: Session) -> str:
    """One chronological row per movement or trade, with sell P&L attached."""
    events: list[tuple[datetime, int, int, tuple[object, ...]]] = []
    for movement in session.scalars(select(CapitalMovement)):
        when = _as_utc(movement.occurred_at_utc)
        events.append(
            (
                when,
                0,
                movement.id,
                (
                    movement.movement_type,
                    movement.id,
                    when.isoformat(),
                    "",
                    "",
                    "",
                    movement.amount_paise,
                    "",
                    "",
                    "",
                    movement.note,
                    movement.idempotency_key,
                ),
            )
        )
    report = read_realized_pnl_report(session)
    pnl_by_sale: dict[int, int] = {}
    for match in report.matches:
        pnl_by_sale[match.sell_transaction_id] = (
            pnl_by_sale.get(match.sell_transaction_id, 0) + match.realized_pnl_paise
        )
    vault_by_sale = {
        entry.sell_transaction_id: entry.amount_paise
        for entry in session.scalars(select(ProfitVaultEntry))
    }
    for trade in session.scalars(select(LedgerTransaction)):
        when = _as_utc(trade.transacted_at_utc)
        events.append(
            (
                when,
                1,
                trade.id,
                (
                    trade.transaction_type,
                    trade.id,
                    when.isoformat(),
                    trade.symbol,
                    trade.quantity,
                    trade.price_paise,
                    "",
                    trade.fees_paise,
                    pnl_by_sale.get(trade.id, "")
                    if trade.transaction_type == "sell"
                    else "",
                    vault_by_sale.get(trade.id, "")
                    if trade.transaction_type == "sell"
                    else "",
                    trade.note,
                    trade.idempotency_key,
                ),
            )
        )
    events.sort(key=lambda item: item[:3])
    return _csv_text(LEDGER_COLUMNS, [item[3] for item in events])


def export_fifo_matches_csv(
    session: Session,
    from_date: date | None = None,
    to_date: date | None = None,
    financial_year: int | None = None,
) -> str:
    """Export one row per FIFO match, optionally filtered by sale date."""
    report = read_realized_pnl_report(session, from_date, to_date, financial_year)
    rows: list[tuple[object, ...]] = [
        (
            item.sell_transaction_id,
            item.buy_transaction_id,
            item.holding_lot_id,
            item.symbol,
            item.quantity,
            item.acquired_at_utc.isoformat(),
            item.realized_at_utc.isoformat(),
            item.holding_period_days,
            item.cost_basis_paise,
            item.gross_proceeds_paise,
            item.sell_fees_paise,
            item.net_proceeds_paise,
            item.realized_pnl_paise,
        )
        for item in report.matches
    ]
    return _csv_text(MATCH_COLUMNS, rows)
