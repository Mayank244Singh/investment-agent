"""Provider-neutral, append-only observations; canonical selection stays explicit."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.prices import (
    DailyPrice,
    IngestionRun,
    Instrument,
    InstrumentMapping,
    SourceObservation,
)


class MarketDataError(ValueError):
    """Invalid observation, missing identity, or ambiguous mapping."""


@dataclass(frozen=True)
class ObservationInput:
    """Only supplied fields; no inferred provider timestamps or previous close."""

    provider: str
    source_record_key: str
    provider_instrument_key: str | None
    symbol: str
    exchange: str
    series: str
    trade_date: date
    open_price: str | Decimal
    high_price: str | Decimal
    low_price: str | Decimal
    close_price: str | Decimal
    volume: int
    previous_close_price: str | Decimal | None = None
    event_at_utc: datetime | None = None
    retrieved_at_utc: datetime | None = None
    available_at_utc: datetime | None = None


@dataclass(frozen=True)
class NormalizedObservation:
    """Validated integer-paise fields and source identity."""

    provider: str
    source_record_key: str
    provider_instrument_key: str | None
    symbol: str
    exchange: str
    series: str
    trade_date: date
    open_paise: int
    high_paise: int
    low_paise: int
    close_paise: int
    previous_close_paise: int | None
    volume: int
    event_at_utc: datetime | None = None
    retrieved_at_utc: datetime | None = None
    available_at_utc: datetime | None = None


def decimal_paise(value: str | Decimal, label: str) -> int:
    """Reject binary floats, fractional paise, infinities, and SQLite overflow."""
    if isinstance(value, float) or isinstance(value, bool):
        raise MarketDataError(f"{label} must be a decimal string or Decimal")
    try:
        decimal = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise MarketDataError(f"{label} must be a decimal price") from exc
    if not decimal.is_finite() or decimal <= 0:
        raise MarketDataError(f"{label} must be positive and finite")
    exponent = decimal.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -2:
        raise MarketDataError(f"{label} has more than two decimal places")
    paise = int(decimal * 100)
    if paise > 2**63 - 1:
        raise MarketDataError(f"{label} exceeds database integer range")
    return paise


def normalize_observation(value: ObservationInput) -> NormalizedObservation:
    """Convert one supplied candle without deriving unavailable facts."""
    normalized = NormalizedObservation(
        provider=value.provider,
        source_record_key=value.source_record_key,
        provider_instrument_key=value.provider_instrument_key,
        symbol=value.symbol.strip().upper(),
        exchange=value.exchange.strip().upper(),
        series=value.series.strip().upper(),
        trade_date=value.trade_date,
        open_paise=decimal_paise(value.open_price, "open price"),
        high_paise=decimal_paise(value.high_price, "high price"),
        low_paise=decimal_paise(value.low_price, "low price"),
        close_paise=decimal_paise(value.close_price, "close price"),
        previous_close_paise=(
            None
            if value.previous_close_price is None
            else decimal_paise(value.previous_close_price, "previous close")
        ),
        volume=value.volume,
        event_at_utc=value.event_at_utc,
        retrieved_at_utc=value.retrieved_at_utc,
        available_at_utc=value.available_at_utc,
    )
    _validate_normalized(normalized)
    return normalized


def _validate_normalized(value: NormalizedObservation) -> None:
    if not value.provider or len(value.provider) > 64 or not value.source_record_key:
        raise MarketDataError("provider and source record key are required")
    if len(value.source_record_key) > 512:
        raise MarketDataError("source record key is too long")
    if not value.symbol or len(value.symbol) > 20 or not value.exchange:
        raise MarketDataError("symbol and exchange are required")
    if value.series != "EQ":
        raise MarketDataError("only EQ observations are supported")
    if not (value.low_paise <= value.open_paise <= value.high_paise):
        raise MarketDataError("open price is outside low-high range")
    if not (value.low_paise <= value.close_paise <= value.high_paise):
        raise MarketDataError("close price is outside low-high range")
    for label, amount in (
        ("open", value.open_paise),
        ("high", value.high_paise),
        ("low", value.low_paise),
        ("close", value.close_paise),
    ):
        if type(amount) is not int or amount <= 0 or amount > 2**63 - 1:
            raise MarketDataError(f"invalid {label} paise")
    if value.previous_close_paise is not None and (
        type(value.previous_close_paise) is not int
        or value.previous_close_paise <= 0
        or value.previous_close_paise > 2**63 - 1
    ):
        raise MarketDataError("invalid optional previous close")
    if type(value.volume) is not int or not 0 <= value.volume <= 2**63 - 1:
        raise MarketDataError("volume must be a non-negative integer")
    for label, timestamp in (
        ("event", value.event_at_utc),
        ("retrieved", value.retrieved_at_utc),
        ("available", value.available_at_utc),
    ):
        if timestamp is not None and timestamp.utcoffset() is None:
            raise MarketDataError(f"{label} timestamp must include a timezone")


def add_verified_mapping(
    session: Session,
    *,
    instrument_id: int,
    provider: str,
    provider_instrument_key: str | None,
    symbol: str,
    exchange: str,
    series: str,
    valid_from: date,
    valid_to: date,
) -> InstrumentMapping:
    """Add a reviewed, bounded alias; overlapping mappings fail closed."""
    normalized_symbol = symbol.strip().upper()
    normalized_exchange = exchange.strip().upper()
    if valid_to < valid_from or series != "EQ" or not provider or not normalized_symbol:
        raise MarketDataError("invalid verified mapping")
    if session.get(Instrument, instrument_id) is None:
        raise MarketDataError("unknown internal instrument")
    existing = session.scalars(
        select(InstrumentMapping).where(
            InstrumentMapping.provider == provider,
            InstrumentMapping.exchange == normalized_exchange,
            InstrumentMapping.series == series,
            InstrumentMapping.identity_status == "verified",
        )
    )
    for mapping in existing:
        same_alias = mapping.symbol == normalized_symbol
        same_key = (
            provider_instrument_key is not None
            and mapping.provider_instrument_key == provider_instrument_key
        )
        if (same_alias or same_key) and (
            mapping.valid_from is None
            or mapping.valid_to is None
            or not (valid_to < mapping.valid_from or valid_from > mapping.valid_to)
        ):
            raise MarketDataError("ambiguous overlapping provider mapping")
    mapping = InstrumentMapping(
        instrument_id=instrument_id,
        provider=provider,
        provider_instrument_key=provider_instrument_key,
        symbol=normalized_symbol,
        exchange=normalized_exchange,
        series=series,
        valid_from=valid_from,
        valid_to=valid_to,
        identity_status="verified",
    )
    session.add(mapping)
    session.flush()
    return mapping


def resolve_instrument(
    session: Session,
    *,
    provider: str,
    provider_instrument_key: str | None,
    symbol: str,
    exchange: str,
    series: str,
    trade_date: date,
) -> Instrument:
    """Require one verified time-valid mapping; never guess from a ticker."""
    query = select(InstrumentMapping).where(
        InstrumentMapping.provider == provider,
        InstrumentMapping.symbol == symbol.strip().upper(),
        InstrumentMapping.exchange == exchange.strip().upper(),
        InstrumentMapping.series == series,
        InstrumentMapping.identity_status == "verified",
        InstrumentMapping.valid_from <= trade_date,
        InstrumentMapping.valid_to >= trade_date,
    )
    if provider_instrument_key is not None:
        query = query.where(
            InstrumentMapping.provider_instrument_key == provider_instrument_key
        )
    else:
        query = query.where(InstrumentMapping.provider_instrument_key.is_(None))
    matches = list(session.scalars(query))
    if len(matches) != 1:
        raise MarketDataError("instrument mapping is missing or ambiguous")
    instrument = session.get(Instrument, matches[0].instrument_id)
    if instrument is None:
        raise MarketDataError("instrument mapping has no internal instrument")
    return instrument


def record_observation(
    session: Session,
    *,
    ingestion_run: IngestionRun,
    value: NormalizedObservation,
    raw_payload: Mapping[str, str],
    instrument_id: int,
    selected_for_canonical: bool = False,
) -> tuple[SourceObservation, bool]:
    """Append once per provider/key/payload; never change a canonical bar."""
    _validate_normalized(value)
    if ingestion_run.provider != value.provider:
        raise MarketDataError("ingestion run provider differs from observation")
    if session.get(Instrument, instrument_id) is None:
        raise MarketDataError("unknown internal instrument")
    if not raw_payload:
        raise MarketDataError("raw payload cannot be empty")
    if any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in raw_payload.items()
    ):
        raise MarketDataError("raw payload fields must be strings")
    raw = json.dumps(dict(raw_payload), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    existing = session.scalar(
        select(SourceObservation).where(
            SourceObservation.provider == value.provider,
            SourceObservation.source_record_key == value.source_record_key,
            SourceObservation.payload_sha256 == digest,
        )
    )
    if existing is not None:
        if (
            existing.instrument_id != instrument_id
            or existing.trade_date != value.trade_date
            or existing.provider_instrument_key != value.provider_instrument_key
            or existing.symbol != value.symbol
            or existing.exchange != value.exchange
            or existing.series != value.series
            or any(
                getattr(existing, field) != getattr(value, field)
                for field in (
                    "open_paise",
                    "high_paise",
                    "low_paise",
                    "close_paise",
                    "previous_close_paise",
                    "volume",
                )
            )
        ):
            raise MarketDataError(
                "duplicate source identity has different normalized facts"
            )
        return existing, False
    canonical = session.scalar(
        select(DailyPrice).where(
            DailyPrice.instrument_id == instrument_id,
            DailyPrice.trade_date == value.trade_date,
        )
    )
    if selected_for_canonical and canonical is not None:
        raise MarketDataError("canonical bar already exists; selection requires review")
    reconciliation = "unselected"
    if selected_for_canonical:
        reconciliation = "selected"
    elif canonical is not None:
        equal = all(
            getattr(canonical, field) == getattr(value, field)
            for field in (
                "open_paise",
                "high_paise",
                "low_paise",
                "close_paise",
                "volume",
            )
        ) and (
            value.previous_close_paise is None
            or canonical.previous_close_paise is None
            or value.previous_close_paise == canonical.previous_close_paise
        )
        reconciliation = "agreement" if equal else "conflict"
    observation = SourceObservation(
        instrument_id=instrument_id,
        ingestion_run_id=ingestion_run.id,
        provider=value.provider,
        provider_instrument_key=value.provider_instrument_key,
        source_record_key=value.source_record_key,
        payload_sha256=digest,
        symbol=value.symbol,
        exchange=value.exchange,
        series=value.series,
        trade_date=value.trade_date,
        event_at_utc=(
            value.event_at_utc.astimezone(UTC) if value.event_at_utc else None
        ),
        retrieved_at_utc=(
            value.retrieved_at_utc.astimezone(UTC) if value.retrieved_at_utc else None
        ),
        available_at_utc=(
            value.available_at_utc.astimezone(UTC) if value.available_at_utc else None
        ),
        open_paise=value.open_paise,
        high_paise=value.high_paise,
        low_paise=value.low_paise,
        close_paise=value.close_paise,
        previous_close_paise=value.previous_close_paise,
        volume=value.volume,
        origin="source",
        reconciliation=reconciliation,
        raw_payload_json=raw,
    )
    session.add(observation)
    session.flush()
    return observation, True


def record_mapped_observation(
    session: Session,
    *,
    ingestion_run: IngestionRun,
    value: ObservationInput,
    raw_payload: Mapping[str, str],
) -> tuple[SourceObservation, bool]:
    """Small provider-neutral entry point; mapping must already be verified."""
    normalized = normalize_observation(value)
    instrument = resolve_instrument(
        session,
        provider=value.provider,
        provider_instrument_key=value.provider_instrument_key,
        symbol=normalized.symbol,
        exchange=normalized.exchange,
        series=normalized.series,
        trade_date=normalized.trade_date,
    )
    return record_observation(
        session,
        ingestion_run=ingestion_run,
        value=normalized,
        raw_payload=raw_payload,
        instrument_id=instrument.id,
    )
