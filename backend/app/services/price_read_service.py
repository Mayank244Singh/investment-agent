"""Read-only as-of valuations and imported price coverage."""

import re
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models.prices import (
    DailyPrice,
    ImportBatch,
    IngestionRun,
    Instrument,
    SourceObservation,
)
from app.modules.portfolio import LedgerService
from app.modules.portfolio.errors import LedgerValidationError
from app.modules.portfolio.ledger_service import HoldingSummary
from app.schemas.prices import (
    HoldingValuationResponse,
    ImportBatchCoverageResponse,
    LatestEqCloseResponse,
    PortfolioValuationResponse,
    PriceCoverageResponse,
    SymbolPriceCoverageResponse,
    UnavailableReason,
    ValuationTotalsResponse,
)

MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")


def read_latest_eq_close(
    session: Session, symbol: str, staleness_limit_days: int
) -> LatestEqCloseResponse:
    """Read a dated reference close; never infer a price for an unknown symbol."""
    normalized = symbol.strip().upper()
    if len(normalized) > 20 or not re.fullmatch(r"[A-Z0-9][A-Z0-9.&-]*", normalized):
        raise LedgerValidationError("symbol contains unsupported characters")
    row = session.execute(
        select(DailyPrice, ImportBatch, IngestionRun)
        .join(Instrument, DailyPrice.instrument_id == Instrument.id)
        .outerjoin(ImportBatch, DailyPrice.import_batch_id == ImportBatch.id)
        .outerjoin(
            SourceObservation,
            DailyPrice.source_observation_id == SourceObservation.id,
        )
        .outerjoin(IngestionRun, SourceObservation.ingestion_run_id == IngestionRun.id)
        .where(Instrument.symbol == normalized, Instrument.series == "EQ")
        .order_by(DailyPrice.trade_date.desc())
        .limit(1)
    ).first()
    if row is None:
        return LatestEqCloseResponse(
            symbol=normalized,
            status="no_eq_price",
            price_date=None,
            close_paise=None,
            source_label=None,
            file_name=None,
            staleness_limit_days=staleness_limit_days,
        )
    price, batch, run = row
    return LatestEqCloseResponse(
        symbol=normalized,
        status="available",
        price_date=price.trade_date,
        close_paise=price.close_paise,
        source_label=batch.source_label if batch else (run.provider if run else None),
        file_name=batch.file_name if batch else None,
        staleness_limit_days=staleness_limit_days,
    )


def read_portfolio_valuation(
    session: Session, staleness_limit_days: int, as_of_date: date | None = None
) -> PortfolioValuationResponse:
    """Value open holdings only when an as-of EQ close is recent enough."""
    if staleness_limit_days <= 0:
        raise ValueError("price staleness limit must be positive")
    effective_date = as_of_date or datetime.now(MARKET_TIMEZONE).date()
    summary = LedgerService(session).get_summary()
    holdings = [
        _value_holding(session, item, effective_date, staleness_limit_days)
        for item in summary.holdings
    ]
    priced = [item for item in holdings if item.status == "priced"]
    unpriced = [item for item in holdings if item.status == "unavailable"]
    return PortfolioValuationResponse(
        as_of_date=effective_date,
        staleness_limit_days=staleness_limit_days,
        holdings=holdings,
        totals=ValuationTotalsResponse(
            priced_holdings_count=len(priced),
            unpriced_holdings_count=len(unpriced),
            priced_cost_basis_paise=sum(item.cost_basis_paise for item in priced),
            priced_market_value_paise=sum(
                item.market_value_paise or 0 for item in priced
            ),
            priced_unrealized_pnl_paise=sum(
                item.unrealized_pnl_paise or 0 for item in priced
            ),
            unpriced_cost_basis_paise=sum(item.cost_basis_paise for item in unpriced),
            total_open_cost_basis_paise=summary.open_cost_basis_paise,
        ),
    )


def _value_holding(
    session: Session,
    holding: HoldingSummary,
    as_of_date: date,
    staleness_limit_days: int,
) -> HoldingValuationResponse:
    instrument = session.scalar(
        select(Instrument).where(
            Instrument.symbol == holding.symbol, Instrument.series == "EQ"
        )
    )
    if instrument is None:
        return _unavailable(holding, "no_eq_price")

    price_row = session.execute(
        select(DailyPrice, ImportBatch, IngestionRun)
        .outerjoin(ImportBatch, DailyPrice.import_batch_id == ImportBatch.id)
        .outerjoin(
            SourceObservation,
            DailyPrice.source_observation_id == SourceObservation.id,
        )
        .outerjoin(IngestionRun, SourceObservation.ingestion_run_id == IngestionRun.id)
        .where(
            DailyPrice.instrument_id == instrument.id,
            DailyPrice.trade_date <= as_of_date,
        )
        .order_by(DailyPrice.trade_date.desc())
        .limit(1)
    ).first()
    if price_row is None:
        any_price = session.scalar(
            select(DailyPrice.id)
            .where(DailyPrice.instrument_id == instrument.id)
            .limit(1)
        )
        return _unavailable(
            holding,
            "no_price_on_or_before_as_of" if any_price is not None else "no_eq_price",
        )

    price, batch, run = price_row
    source_label = batch.source_label if batch else (run.provider if run else None)
    file_name = batch.file_name if batch else None
    age_days = (as_of_date - price.trade_date).days
    if age_days > staleness_limit_days:
        return _unavailable(
            holding,
            "stale_price",
            last_price_date=price.trade_date,
            price_age_days=age_days,
            source_label=source_label,
            file_name=file_name,
        )

    market_value = holding.quantity * price.close_paise
    return HoldingValuationResponse(
        symbol=holding.symbol,
        quantity=holding.quantity,
        cost_basis_paise=holding.cost_basis_paise,
        status="priced",
        unavailable_reason=None,
        last_price_date=price.trade_date,
        price_age_days=age_days,
        close_paise=price.close_paise,
        market_value_paise=market_value,
        unrealized_pnl_paise=market_value - holding.cost_basis_paise,
        source_label=source_label,
        file_name=file_name,
    )


def _unavailable(
    holding: HoldingSummary,
    reason: UnavailableReason,
    *,
    last_price_date: date | None = None,
    price_age_days: int | None = None,
    source_label: str | None = None,
    file_name: str | None = None,
) -> HoldingValuationResponse:
    return HoldingValuationResponse(
        symbol=holding.symbol,
        quantity=holding.quantity,
        cost_basis_paise=holding.cost_basis_paise,
        status="unavailable",
        unavailable_reason=reason,
        last_price_date=last_price_date,
        price_age_days=price_age_days,
        close_paise=None,
        market_value_paise=None,
        unrealized_pnl_paise=None,
        source_label=source_label,
        file_name=file_name,
    )


def read_price_coverage(session: Session) -> PriceCoverageResponse:
    """List each symbol's latest stored close and every import batch."""
    rows = session.execute(
        select(Instrument.symbol, func.max(DailyPrice.trade_date))
        .outerjoin(DailyPrice, DailyPrice.instrument_id == Instrument.id)
        .where(Instrument.series == "EQ")
        .group_by(Instrument.id, Instrument.symbol)
        .order_by(Instrument.symbol)
    )
    batches = session.scalars(
        select(ImportBatch).order_by(
            ImportBatch.imported_at_utc.desc(), ImportBatch.id.desc()
        )
    )
    return PriceCoverageResponse(
        symbols=[
            SymbolPriceCoverageResponse(symbol=symbol, latest_price_date=latest)
            for symbol, latest in rows
        ],
        import_batches=[
            ImportBatchCoverageResponse(
                id=batch.id,
                file_name=batch.file_name,
                source_label=batch.source_label,
                total_rows=batch.total_rows,
                imported_rows=batch.imported_rows,
                skipped_rows=batch.skipped_rows,
                rejected_rows=batch.rejected_rows,
                status=batch.status,
                imported_at_utc=_as_utc(batch.imported_at_utc),
            )
            for batch in batches
        ],
    )


def _as_utc(value: datetime) -> datetime:
    """Restore UTC information omitted by SQLite's datetime adapter."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
