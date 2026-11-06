"""Read-only, exact comparison of Upstox observations with NSE canonical bars."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.prices import DailyPrice, Instrument, SourceObservation

UPSTOX_PROVIDER = "upstox"
PriceState = Literal[
    "EXACT_MATCH",
    "PRICE_MATCH_VOLUME_DIFF",
    "PRICE_DIFF",
    "MISSING_NSE",
    "MISSING_UPSTOX",
    "REVISION_AMBIGUOUS",
]
PRICE_FIELDS = ("open_paise", "high_paise", "low_paise", "close_paise")


@dataclass(frozen=True)
class FieldDifference:
    """Signed and absolute Upstox-minus-NSE differences, without tolerance."""

    field: str
    nse_value: int
    upstox_value: int
    difference: int
    absolute_difference: int
    percentage_difference: Decimal | None


@dataclass(frozen=True)
class ReconciliationRow:
    symbol: str
    instrument_id: int
    trade_date: date
    state: PriceState
    nse_price_id: int | None
    nse_import_batch_id: int | None
    upstox_observation_ids: tuple[int, ...]
    upstox_instrument_keys: tuple[str, ...]
    nse_previous_close_present: bool | None
    upstox_previous_close_present: bool | None
    upstox_event_at_utc: datetime | None
    differences: tuple[FieldDifference, ...]


@dataclass(frozen=True)
class ReconciliationCounts:
    nse_rows: int
    upstox_rows: int
    exact_matches: int
    exact_ohlc_matches: int
    close_matches: int
    price_disagreements: int
    volume_disagreements: int
    missing_nse: int
    missing_upstox: int
    revision_ambiguous: int


@dataclass(frozen=True)
class SymbolReconciliation:
    symbol: str
    instrument_id: int
    isin: str | None
    upstox_instrument_keys: tuple[str, ...]
    counts: ReconciliationCounts
    rows: tuple[ReconciliationRow, ...]


@dataclass(frozen=True)
class ReconciliationReport:
    dates: tuple[date, ...]
    symbols: tuple[SymbolReconciliation, ...]
    aggregate: ReconciliationCounts


def reconcile_prices(
    session: Session, instruments: Mapping[str, int], dates: Sequence[date]
) -> ReconciliationReport:
    """Compare selected NSE bars and all Upstox revisions without writing data."""
    if not instruments or not dates:
        raise ValueError("instruments and dates are required")
    if len(set(instruments.values())) != len(instruments):
        raise ValueError("instrument IDs must be unique")
    if len(set(dates)) != len(dates):
        raise ValueError("trading dates must be unique")
    requested_dates = tuple(sorted(dates))
    by_symbol: dict[str, Instrument] = {}
    for symbol, instrument_id in instruments.items():
        instrument = session.get(Instrument, instrument_id)
        if (
            instrument is None
            or instrument.symbol != symbol.strip().upper()
            or instrument.series != "EQ"
        ):
            raise ValueError(f"instrument identity is missing or mismatched: {symbol}")
        by_symbol[instrument.symbol] = instrument
    if len(by_symbol) != len(instruments):
        raise ValueError("duplicate normalized symbols")

    ids = tuple(instruments.values())
    prices = session.scalars(
        select(DailyPrice).where(
            DailyPrice.instrument_id.in_(ids),
            DailyPrice.trade_date.in_(requested_dates),
            DailyPrice.import_batch_id.is_not(None),
        )
    )
    nse_by_key = {(item.instrument_id, item.trade_date): item for item in prices}
    observations = session.scalars(
        select(SourceObservation).where(
            SourceObservation.provider == UPSTOX_PROVIDER,
            SourceObservation.instrument_id.in_(ids),
            SourceObservation.trade_date >= requested_dates[0],
            SourceObservation.trade_date <= requested_dates[-1],
        )
    )
    upstox_by_key: dict[tuple[int, date], list[SourceObservation]] = defaultdict(list)
    for item in observations:
        upstox_by_key[item.instrument_id, item.trade_date].append(item)

    symbols: list[SymbolReconciliation] = []
    for symbol, instrument in sorted(by_symbol.items()):
        rows: list[ReconciliationRow] = []
        comparison_dates = sorted(
            set(requested_dates)
            | {
                day
                for instrument_id, day in upstox_by_key
                if instrument_id == instrument.id
            }
        )
        for day in comparison_dates:
            nse = nse_by_key.get((instrument.id, day))
            upstox = sorted(
                upstox_by_key.get((instrument.id, day), []), key=lambda item: item.id
            )
            if nse is None and not upstox:
                raise ValueError(f"neither source has {symbol} on {day.isoformat()}")
            rows.append(_compare(symbol, instrument.id, day, nse, upstox))
        keys = tuple(
            sorted({key for row in rows for key in row.upstox_instrument_keys})
        )
        symbols.append(
            SymbolReconciliation(
                symbol=symbol,
                instrument_id=instrument.id,
                isin=instrument.isin,
                upstox_instrument_keys=keys,
                counts=_counts(rows),
                rows=tuple(rows),
            )
        )
    all_rows = tuple(row for symbol in symbols for row in symbol.rows)
    return ReconciliationReport(requested_dates, tuple(symbols), _counts(all_rows))


def _compare(
    symbol: str,
    instrument_id: int,
    day: date,
    nse: DailyPrice | None,
    upstox: list[SourceObservation],
) -> ReconciliationRow:
    keys = tuple(
        sorted(
            {
                item.provider_instrument_key
                for item in upstox
                if item.provider_instrument_key is not None
            }
        )
    )
    state: PriceState
    differences: tuple[FieldDifference, ...] = ()
    if len(upstox) > 1:
        state = "REVISION_AMBIGUOUS"
    elif nse is None:
        state = "MISSING_NSE"
    elif not upstox:
        state = "MISSING_UPSTOX"
    else:
        observation = upstox[0]
        differences = tuple(
            _difference(field, getattr(nse, field), getattr(observation, field))
            for field in (*PRICE_FIELDS, "volume")
            if getattr(nse, field) != getattr(observation, field)
        )
        if any(item.field in PRICE_FIELDS for item in differences):
            state = "PRICE_DIFF"
        elif differences:
            state = "PRICE_MATCH_VOLUME_DIFF"
        else:
            state = "EXACT_MATCH"
    return ReconciliationRow(
        symbol=symbol,
        instrument_id=instrument_id,
        trade_date=day,
        state=state,
        nse_price_id=nse.id if nse is not None else None,
        nse_import_batch_id=nse.import_batch_id if nse is not None else None,
        upstox_observation_ids=tuple(item.id for item in upstox),
        upstox_instrument_keys=keys,
        nse_previous_close_present=(
            nse.previous_close_paise is not None if nse is not None else None
        ),
        upstox_previous_close_present=(
            upstox[0].previous_close_paise is not None if len(upstox) == 1 else None
        ),
        upstox_event_at_utc=(
            _as_utc(upstox[0].event_at_utc) if len(upstox) == 1 else None
        ),
        differences=differences,
    )


def _difference(field: str, nse_value: int, upstox_value: int) -> FieldDifference:
    delta = upstox_value - nse_value
    return FieldDifference(
        field=field,
        nse_value=nse_value,
        upstox_value=upstox_value,
        difference=delta,
        absolute_difference=abs(delta),
        percentage_difference=(
            Decimal(abs(delta) * 100) / Decimal(abs(nse_value))
            if nse_value != 0
            else None
        ),
    )


def _counts(rows: Sequence[ReconciliationRow]) -> ReconciliationCounts:
    comparable = [
        row
        for row in rows
        if row.nse_price_id is not None and len(row.upstox_observation_ids) == 1
    ]
    return ReconciliationCounts(
        nse_rows=sum(row.nse_price_id is not None for row in rows),
        upstox_rows=sum(len(row.upstox_observation_ids) for row in rows),
        exact_matches=sum(row.state == "EXACT_MATCH" for row in rows),
        exact_ohlc_matches=sum(
            not any(item.field in PRICE_FIELDS for item in row.differences)
            for row in comparable
        ),
        close_matches=sum(
            not any(item.field == "close_paise" for item in row.differences)
            for row in comparable
        ),
        price_disagreements=sum(row.state == "PRICE_DIFF" for row in rows),
        volume_disagreements=sum(
            any(item.field == "volume" for item in row.differences)
            for row in comparable
        ),
        missing_nse=sum(row.nse_price_id is None for row in rows),
        missing_upstox=sum(not row.upstox_observation_ids for row in rows),
        revision_ambiguous=sum(row.state == "REVISION_AMBIGUOUS" for row in rows),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
