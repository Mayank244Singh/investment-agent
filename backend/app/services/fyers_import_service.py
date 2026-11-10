"""Checkpointed FYERS daily import into existing source and canonical tables."""

import base64
import binascii
import fcntl
import hashlib
import json
import os
import secrets
import stat
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models.prices import DailyPrice, IngestionRun, Instrument, InstrumentMapping
from app.modules.research.contracts import Timeframe
from app.modules.research.readiness import CalendarEvidence
from app.services.fyers_historical_provider import (
    FyersBatch,
    FyersHistoricalDataProvider,
    verify_master_mapping,
)
from app.services.market_data_service import (
    NormalizedObservation,
    add_verified_mapping,
    record_observation,
    resolve_instrument,
)


class FyersImportError(ValueError):
    """A pilot import cannot safely proceed or resume."""


@dataclass(frozen=True)
class IdentityReview:
    symbol: str
    ticker: str
    isin: str
    valid_from: date
    valid_to: date
    evidence_note: str

    def __post_init__(self) -> None:
        if (
            not self.evidence_note.strip()
            or self.valid_to < self.valid_from
        ):
            raise FyersImportError("bounded historical identity review is required")


@dataclass(frozen=True)
class FyersImportResult:
    symbol: str
    from_date: date
    to_date: date
    fetched: bool
    resumed_raw: bool
    checkpoint_skipped: bool
    candles: int
    observations_created: int
    duplicates: int
    canonical_created: int
    agreements: int
    conflicts: int
    missing_sessions: tuple[date, ...]
    response_sha256: str


def _expected_days(
    evidence: CalendarEvidence, first: date, last: date,
) -> tuple[date, ...]:
    sessions = {
        item.day: item for item in evidence.calendar.sessions
        if first <= item.day <= last
    }
    holidays = evidence.calendar.holidays
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        if day.weekday() < 5 and day not in sessions and day not in holidays:
            raise FyersImportError(f"calendar does not classify {day}")
    if not sessions:
        raise FyersImportError("calendar has no sessions in the requested window")
    return tuple(
        day for day, session in sorted(sessions.items())
        if not any(
            halt.start_utc <= session.opens_utc
            and halt.end_utc >= session.closes_utc
            for halt in evidence.halts
        )
    )


def _raw_path(directory: Path, review: IdentityReview, first: date,
              last: date) -> Path:
    return directory / f"{review.symbol}_{first:%Y%m%d}_{last:%Y%m%d}.json"


def _private_directory(directory: Path) -> None:
    metadata = directory.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077):
        raise FyersImportError("raw-data directory must be private (mode 700)")


def _read_raw(path: Path, source_ref: str,
              review: IdentityReview) -> tuple[bytes, datetime, str]:
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077):
        raise FyersImportError("raw response file must be private and regular")
    if metadata.st_size > 3_000_000:
        raise FyersImportError("raw response envelope exceeds size limit")
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(envelope, dict) or set(envelope) != {
            "source_ref", "sha256", "retrieved_at_utc", "response_base64",
            "identity_evidence", "symbol", "ticker", "isin",
        } or (
            envelope["source_ref"] != source_ref
            or envelope["symbol"] != review.symbol
            or envelope["ticker"] != review.ticker
            or envelope["isin"] != review.isin
            or envelope["identity_evidence"] != review.evidence_note
        ):
            raise ValueError("raw response provenance mismatch")
        body = base64.b64decode(envelope["response_base64"], validate=True)
        digest = hashlib.sha256(body).hexdigest()
        if len(body) > 2_000_000 or digest != envelope["sha256"]:
            raise ValueError("raw response hash mismatch")
        retrieved = datetime.fromisoformat(envelope["retrieved_at_utc"])
        if retrieved.utcoffset() != timedelta(0):
            raise ValueError("raw response retrieval timestamp is not UTC")
        return body, retrieved, digest
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, binascii.Error):
        raise FyersImportError("raw response envelope is invalid") from None


def _write_raw(path: Path, batch: FyersBatch, review: IdentityReview) -> None:
    envelope = {
        "source_ref": batch.source_ref,
        "sha256": batch.response_sha256,
        "retrieved_at_utc": batch.retrieved_at_utc.isoformat(),
        "response_base64": base64.b64encode(batch.raw_response).decode("ascii"),
        "identity_evidence": review.evidence_note,
        "symbol": review.symbol,
        "ticker": review.ticker,
        "isin": review.isin,
    }
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(json.dumps(envelope, sort_keys=True).encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
        os.link(temporary, path)  # Never replace an earlier raw response.
    finally:
        temporary.unlink(missing_ok=True)


def import_fyers_daily_window(
    engine: Engine,
    provider: FyersHistoricalDataProvider,
    *,
    review: IdentityReview,
    master_record: Mapping[str, object],
    first: date,
    last: date,
    calendar: CalendarEvidence,
    raw_dir: Path,
) -> FyersImportResult:
    """One bounded window; raw file first, then one atomic database transaction."""
    if (
        first > last or (last - first).days >= 31
        or first < review.valid_from or last > review.valid_to
    ):
        raise FyersImportError("window is outside the reviewed 31-day identity range")
    expected = set(_expected_days(calendar, first, last))
    _private_directory(raw_dir)
    lock_path = raw_dir / ".fyers-import.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor) as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return _import_locked(
                engine, provider, review, master_record, first, last,
                expected, raw_dir,
            )
    except (BlockingIOError, OSError) as exc:
        raise FyersImportError("FYERS import lock is unavailable") from exc


def _import_locked(
    engine: Engine,
    provider: FyersHistoricalDataProvider,
    review: IdentityReview,
    master_record: Mapping[str, object],
    first: date,
    last: date,
    expected: set[date],
    raw_dir: Path,
) -> FyersImportResult:
    with Session(engine) as session:
        matches = list(session.scalars(select(Instrument).where(
            Instrument.symbol == review.symbol, Instrument.series == "EQ",
        )))
        if len(matches) != 1 or matches[0].isin != review.isin:
            raise FyersImportError("internal EQ identity is missing or conflicts")
        instrument = verify_master_mapping(
            matches[0], master_record,
            valid_from=review.valid_from, valid_to=review.valid_to,
        )
        if instrument.ticker != review.ticker:
            raise FyersImportError("reviewed FYERS ticker differs from master")
        internal_id = matches[0].id
    source_ref = provider.request_url(instrument, Timeframe.DAY, first, last)
    path = _raw_path(raw_dir, review, first, last)
    with Session(engine) as session:
        runs = list(session.scalars(select(IngestionRun).where(
            IngestionRun.provider == "fyers",
            IngestionRun.source_kind == "provider",
            IngestionRun.source_ref == source_ref,
        )))
        if len(runs) > 1:
            raise FyersImportError("ambiguous FYERS import checkpoint")
        checkpoint = runs[0] if runs else None
    if checkpoint is not None and not path.exists():
        raise FyersImportError("checkpoint raw response is missing")
    resumed_raw = path.exists()
    if resumed_raw:
        body, retrieved, digest = _read_raw(path, source_ref, review)
        if checkpoint is not None and checkpoint.source_sha256 != digest:
            raise FyersImportError("checkpoint and raw response hashes differ")
        batch = provider.parse_response(
            body, instrument, Timeframe.DAY, first, last,
            retrieved_at_utc=retrieved, source_ref=source_ref,
        )
    else:
        batch = provider.fetch(instrument, Timeframe.DAY, first, last)
        if batch.response_sha256 != hashlib.sha256(batch.raw_response).hexdigest():
            raise FyersImportError("FYERS response hash is inconsistent")
    if not all(bar.is_complete for bar in batch.candles):
        raise FyersImportError("incomplete daily candle in FYERS response")
    returned = {bar.trade_date for bar in batch.candles}
    if returned - expected:
        raise FyersImportError("FYERS returned a date outside verified sessions")
    missing = tuple(sorted(expected - returned))
    if checkpoint is not None:
        return FyersImportResult(
            review.symbol, first, last, False, True, True, len(batch.candles),
            0, 0, 0, 0, 0, missing, batch.response_sha256,
        )
    if not resumed_raw:
        _write_raw(path, batch, review)
    created = duplicates = canonical_created = agreements = conflicts = 0
    with Session(engine) as session, session.begin():
        mappings = list(session.scalars(select(InstrumentMapping).where(
            InstrumentMapping.provider == "fyers",
            InstrumentMapping.provider_instrument_key == review.ticker,
            InstrumentMapping.symbol == review.symbol,
            InstrumentMapping.exchange == "NSE",
            InstrumentMapping.series == "EQ",
            InstrumentMapping.identity_status == "verified",
            InstrumentMapping.valid_from <= first,
            InstrumentMapping.valid_to >= last,
        )))
        if len(mappings) > 1 or (mappings and mappings[0].instrument_id != internal_id):
            raise FyersImportError("ambiguous existing FYERS instrument mapping")
        if not mappings:
            add_verified_mapping(
                session, instrument_id=internal_id, provider="fyers",
                provider_instrument_key=review.ticker, symbol=review.symbol,
                exchange="NSE", series="EQ", valid_from=review.valid_from,
                valid_to=review.valid_to,
            )
        run = IngestionRun(
            provider="fyers", source_kind="provider", source_ref=source_ref,
            source_sha256=batch.response_sha256, provenance_kind="source",
        )
        session.add(run)
        session.flush()
        for candle in batch.candles:
            resolved = resolve_instrument(
                session, provider="fyers", provider_instrument_key=review.ticker,
                symbol=review.symbol, exchange="NSE", series="EQ",
                trade_date=candle.trade_date,
            )
            if resolved.id != internal_id:
                raise FyersImportError("FYERS mapping changed during import")
            canonical = session.scalar(select(DailyPrice).where(
                DailyPrice.instrument_id == internal_id,
                DailyPrice.trade_date == candle.trade_date,
            ))
            select_new = canonical is None
            observation, was_created = record_observation(
                session, ingestion_run=run, instrument_id=internal_id,
                value=NormalizedObservation(
                    provider="fyers", source_record_key=candle.source_record_key,
                    provider_instrument_key=review.ticker, symbol=review.symbol,
                    exchange="NSE", series="EQ", trade_date=candle.trade_date,
                    open_paise=candle.open_paise, high_paise=candle.high_paise,
                    low_paise=candle.low_paise, close_paise=candle.close_paise,
                    previous_close_paise=None, volume=candle.volume,
                    event_at_utc=candle.start_utc,
                    retrieved_at_utc=batch.retrieved_at_utc,
                    available_at_utc=None,
                ),
                raw_payload=candle.raw_payload,
                selected_for_canonical=select_new,
            )
            if not was_created:
                if select_new:
                    raise FyersImportError("existing observation lacks canonical bar")
                duplicates += 1
                continue
            created += 1
            if select_new:
                session.add(DailyPrice(
                    instrument_id=internal_id, import_batch_id=None,
                    source_observation_id=observation.id,
                    trade_date=candle.trade_date, open_paise=candle.open_paise,
                    high_paise=candle.high_paise, low_paise=candle.low_paise,
                    close_paise=candle.close_paise, previous_close_paise=None,
                    volume=candle.volume, price_basis="unadjusted",
                ))
                canonical_created += 1
            elif observation.reconciliation == "agreement":
                agreements += 1
            elif observation.reconciliation == "conflict":
                conflicts += 1
        session.flush()
    return FyersImportResult(
        review.symbol, first, last, not resumed_raw, resumed_raw, False,
        len(batch.candles), created, duplicates, canonical_created,
        agreements, conflicts, missing, batch.response_sha256,
    )
