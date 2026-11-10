"""Isolated FYERS staging; provisional candles never enter canonical tables."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import secrets
import sqlite3
import stat
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Mapping

from app.modules.research.contracts import Timeframe
from app.modules.research.readiness import CalendarEvidence
from app.services.fyers_historical_provider import (
    FyersBatch,
    FyersHistoricalDataProvider,
    FyersInstrument,
)
from app.services.fyers_import_service import FyersImportError, _expected_days
from app.services.price_import_service import (
    PriceImportError,
    _file_date,
    _parse_archive,
)


class IdentityState(StrEnum):
    PROVISIONAL = "PROVISIONAL"
    VERIFIED = "VERIFIED"


@dataclass(frozen=True)
class ProvisionalIdentity:
    symbol: str
    ticker: str
    isin: str
    internal_id: int
    nse_evidence_dates: frozenset[date]
    master_sha256: str

    def instrument(self) -> FyersInstrument:
        """A current master match is not a verified historical mapping."""
        return FyersInstrument(
            self.symbol, self.ticker, self.isin, self.internal_id,
            identity_verified=False,
        )


@dataclass(frozen=True)
class Reconciliation:
    day: date
    state: str
    nse_identity_evidence: bool
    differences: Mapping[str, int]


@dataclass(frozen=True)
class ProvisionalResult:
    symbol: str
    candles: int
    fetched: bool
    resumed_raw: bool
    checkpoint_skipped: bool
    missing_sessions: tuple[date, ...]
    missing_nse_identity_dates: tuple[date, ...]
    reconciliation: tuple[Reconciliation, ...]
    response_sha256: str


@dataclass(frozen=True)
class NseReference:
    symbol: str
    isin: str
    day: date
    open_paise: int
    high_paise: int
    low_paise: int
    close_paise: int
    volume: int


def load_local_nse_references(
    directory: Path, sources: list[dict[str, str]],
    identities: Mapping[str, ProvisionalIdentity],
) -> dict[tuple[str, date], NseReference]:
    """Read hash-checked local NSE ZIPs using the existing strict parser."""
    references: dict[tuple[str, date], NseReference] = {}
    if not directory.is_dir() or not sources:
        raise FyersImportError("local NSE reference directory is unavailable")
    for source in sources:
        name = source.get("filename", "")
        if Path(name).name != name:
            raise FyersImportError("NSE evidence filename is invalid")
        try:
            day = _file_date(name)
            path = directory / name
            if path.stat().st_size > 50_000_000:
                raise FyersImportError("NSE reference ZIP exceeds size limit")
            data = path.read_bytes()
            if (day.isoformat() != source.get("trade_date")
                    or hashlib.sha256(data).hexdigest() != source.get("sha256")):
                raise FyersImportError("NSE reference source hash/date differs")
            rows, _, rejected, _ = _parse_archive(
                data, name, day, 50_000_000, False,
            )
        except (OSError, PriceImportError) as exc:
            raise FyersImportError("NSE reference ZIP is invalid") from exc
        if rejected:
            raise FyersImportError("NSE reference ZIP has rejected rows")
        for row in rows:
            identity = identities.get(row.symbol)
            if identity is None:
                continue
            if row.isin != identity.isin:
                raise FyersImportError("NSE reference ISIN conflicts with instrument")
            key = (row.symbol, day)
            if key in references:
                raise FyersImportError("duplicate NSE reference symbol/date")
            references[key] = NseReference(
                row.symbol, row.isin, day, row.open_paise, row.high_paise,
                row.low_paise, row.close_paise, row.volume,
            )
    for identity in identities.values():
        if any(
            (identity.symbol, day) not in references
            for day in identity.nse_evidence_dates
        ):
            raise FyersImportError("NSE identity date lacks a matching local row")
    return references


SCHEMA = """
CREATE TABLE IF NOT EXISTS provisional_runs (
    id INTEGER PRIMARY KEY,
    source_ref TEXT NOT NULL UNIQUE,
    response_sha256 TEXT NOT NULL CHECK(length(response_sha256)=64),
    retrieved_at_utc TEXT NOT NULL,
    symbol TEXT NOT NULL,
    ticker TEXT NOT NULL,
    isin TEXT NOT NULL,
    internal_id INTEGER NOT NULL,
    master_sha256 TEXT NOT NULL CHECK(length(master_sha256)=64),
    first_date TEXT NOT NULL,
    last_date TEXT NOT NULL,
    identity_state TEXT NOT NULL CHECK(identity_state='PROVISIONAL')
);
CREATE TABLE IF NOT EXISTS provisional_candles (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES provisional_runs(id) ON DELETE RESTRICT,
    source_record_key TEXT NOT NULL UNIQUE,
    trade_date TEXT NOT NULL,
    event_at_utc TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),
    open_paise INTEGER NOT NULL CHECK(open_paise>0),
    high_paise INTEGER NOT NULL CHECK(high_paise>0),
    low_paise INTEGER NOT NULL CHECK(low_paise>0),
    close_paise INTEGER NOT NULL CHECK(close_paise>0),
    volume INTEGER NOT NULL CHECK(volume>=0),
    reconciliation_state TEXT NOT NULL,
    nse_identity_evidence INTEGER NOT NULL CHECK(nse_identity_evidence IN (0,1)),
    differences_json TEXT NOT NULL,
    UNIQUE(trade_date, run_id)
);
"""


def initialize_staging(path: Path) -> None:
    """Create a private *separate* SQLite file; never attach canonical data."""
    if path.is_symlink():
        raise FyersImportError("provisional staging path must not be a symlink")
    if not path.exists():
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(descriptor)
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077 or metadata.st_nlink != 1):
        raise FyersImportError("provisional staging database must be private")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(SCHEMA)
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise FyersImportError("provisional staging database is corrupt")


def _raw_path(directory: Path, identity: ProvisionalIdentity,
              first: date, last: date) -> Path:
    name = f"provisional_{identity.symbol}_{first:%Y%m%d}_{last:%Y%m%d}.json"
    return directory / name


def _read_raw(path: Path, expected: Mapping[str, str]) -> tuple[bytes, datetime]:
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077 or metadata.st_nlink != 1
            or metadata.st_size > 3_000_000):
        raise FyersImportError("provisional raw response is not private")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {
            *expected, "response_sha256", "retrieved_at_utc", "response_base64",
        } or any(payload[key] != value for key, value in expected.items()):
            raise ValueError("provisional raw identity mismatch")
        body = base64.b64decode(payload["response_base64"], validate=True)
        if (len(body) > 2_000_000
                or hashlib.sha256(body).hexdigest() != payload["response_sha256"]):
            raise ValueError("provisional raw hash mismatch")
        retrieved = datetime.fromisoformat(payload["retrieved_at_utc"])
        if retrieved.utcoffset() != timedelta(0):
            raise ValueError("provisional raw retrieval time is not UTC")
        return body, retrieved
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, binascii.Error):
        raise FyersImportError("provisional raw response is invalid") from None


def _write_raw(path: Path, batch: FyersBatch, expected: Mapping[str, str]) -> None:
    payload = {
        **expected,
        "response_sha256": batch.response_sha256,
        "retrieved_at_utc": batch.retrieved_at_utc.isoformat(),
        "response_base64": base64.b64encode(batch.raw_response).decode("ascii"),
    }
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps(payload, sort_keys=True).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _reconcile(
    canonical: sqlite3.Connection, identity: ProvisionalIdentity,
    day: date, prices: Mapping[str, int],
    local_reference: NseReference | None,
) -> Reconciliation:
    rows = canonical.execute(
        "SELECT p.open_paise, p.high_paise, p.low_paise, p.close_paise, p.volume "
        "FROM daily_prices p WHERE p.instrument_id=? AND p.trade_date=? "
        "AND p.import_batch_id IS NOT NULL",
        (identity.internal_id, day.isoformat()),
    ).fetchall()
    if len(rows) > 1:
        raise FyersImportError("NSE reference is ambiguous")
    fields = ("open_paise", "high_paise", "low_paise", "close_paise", "volume")
    if rows and local_reference is not None and any(
        stored != getattr(local_reference, field)
        for field, stored in zip(fields, rows[0], strict=True)
    ):
        raise FyersImportError("canonical and local NSE references conflict")
    reference = (
        tuple(getattr(local_reference, field) for field in fields)
        if local_reference is not None else (rows[0] if rows else None)
    )
    if reference is None:
        return Reconciliation(day, "NO_NSE_REFERENCE",
                              day in identity.nse_evidence_dates, {})
    differences = {
        field: prices[field] - value
        for field, value in zip(fields, reference, strict=True)
        if prices[field] != value
    }
    return Reconciliation(
        day, "CONFLICT" if differences else "MATCH",
        day in identity.nse_evidence_dates, differences,
    )


def verify_nse_references(
    canonical_path: Path,
    identities: Mapping[str, ProvisionalIdentity],
    references: Mapping[tuple[str, date], NseReference],
) -> None:
    """Fail offline if a local NSE ZIP and stored NSE reference disagree."""
    with sqlite3.connect(f"{canonical_path.as_uri()}?mode=ro", uri=True) as canonical:
        canonical.execute("PRAGMA query_only=ON")
        for (symbol, day), reference in references.items():
            identity = identities[symbol]
            _reconcile(
                canonical, identity, day,
                {field: getattr(reference, field) for field in (
                    "open_paise", "high_paise", "low_paise", "close_paise",
                    "volume",
                )},
                reference,
            )


def stage_fyers_daily_window(
    canonical_path: Path, staging_path: Path,
    provider: FyersHistoricalDataProvider, *, identity: ProvisionalIdentity,
    first: date, last: date, calendar: CalendarEvidence, raw_dir: Path,
    nse_references: Mapping[tuple[str, date], NseReference] | None = None,
    max_window_days: int = 31,
) -> ProvisionalResult:
    """Stage one bounded response; canonical SQLite is opened mode=ro only."""
    if (first > last or max_window_days not in (31, 366)
            or (last - first).days >= max_window_days):
        raise FyersImportError("provisional window exceeds approved day limit")
    expected_days = set(_expected_days(calendar, first, last))
    if (identity.ticker != f"NSE:{identity.symbol}-EQ"
            or not identity.isin or identity.internal_id <= 0
            or len(identity.master_sha256) != 64):
        raise FyersImportError("current FYERS/NSE identity mismatch")
    if canonical_path.samefile(staging_path):
        raise FyersImportError("provisional storage must be separate")
    staged_file = staging_path.lstat()
    if (not stat.S_ISREG(staged_file.st_mode)
            or staged_file.st_uid != os.getuid()
            or staged_file.st_mode & 0o077
            or staged_file.st_nlink != 1):
        raise FyersImportError("provisional staging database must be private")
    with sqlite3.connect(f"{canonical_path.as_uri()}?mode=ro", uri=True) as canonical:
        canonical.execute("PRAGMA query_only=ON")
        rows = canonical.execute(
            "SELECT id, isin FROM instruments WHERE symbol=? AND series='EQ'",
            (identity.symbol,),
        ).fetchall()
        if rows != [(identity.internal_id, identity.isin)]:
            raise FyersImportError("NSE internal identity changed")
    metadata = raw_dir.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077):
        raise FyersImportError("provisional raw directory must be private")
    instrument = identity.instrument()
    source_ref = provider.request_url(instrument, Timeframe.DAY, first, last)
    expected_raw = {
        "source_ref": source_ref, "symbol": identity.symbol,
        "ticker": identity.ticker, "isin": identity.isin,
        "master_sha256": identity.master_sha256,
        "identity_state": IdentityState.PROVISIONAL.value,
    }
    path = _raw_path(raw_dir, identity, first, last)
    lock_path = raw_dir / ".fyers-provisional.lock"
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor) as lock:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return _stage_locked(
                canonical_path, staging_path, provider, instrument, identity,
                first, last, expected_days, path, expected_raw, source_ref,
                nse_references or {},
            )
    except (BlockingIOError, OSError) as exc:
        raise FyersImportError("provisional import lock is unavailable") from exc


def _stage_locked(
    canonical_path: Path, staging_path: Path,
    provider: FyersHistoricalDataProvider, instrument: FyersInstrument,
    identity: ProvisionalIdentity, first: date, last: date,
    expected_days: set[date], path: Path, expected_raw: Mapping[str, str],
    source_ref: str,
    nse_references: Mapping[tuple[str, date], NseReference],
) -> ProvisionalResult:
    with sqlite3.connect(f"{staging_path.as_uri()}?mode=rw", uri=True) as staging:
        staging.execute("PRAGMA foreign_keys=ON")
        prior = staging.execute(
            "SELECT response_sha256 FROM provisional_runs WHERE source_ref=?",
            (source_ref,),
        ).fetchone()
        if prior is not None and not path.exists():
            raise FyersImportError("provisional checkpoint raw response is missing")
        resumed = path.exists()
        if resumed:
            body, retrieved = _read_raw(path, expected_raw)
            batch = provider.parse_response(
                body, instrument, Timeframe.DAY, first, last,
                retrieved_at_utc=retrieved, source_ref=source_ref,
            )
        else:
            batch = provider.fetch(instrument, Timeframe.DAY, first, last)
            if batch.response_sha256 != hashlib.sha256(batch.raw_response).hexdigest():
                raise FyersImportError("FYERS response hash is inconsistent")
        if not all(candle.is_complete for candle in batch.candles):
            raise FyersImportError("incomplete provisional daily candle")
        returned = {candle.trade_date for candle in batch.candles}
        if returned - expected_days:
            raise FyersImportError("FYERS candle is outside reviewed sessions")
        missing = tuple(sorted(expected_days - returned))
        canonical_uri = f"{canonical_path.as_uri()}?mode=ro"
        with sqlite3.connect(canonical_uri, uri=True) as canonical:
            canonical.execute("PRAGMA query_only=ON")
            rows = canonical.execute(
                "SELECT id, isin FROM instruments WHERE symbol=? AND series='EQ'",
                (identity.symbol,),
            ).fetchall()
            if rows != [(identity.internal_id, identity.isin)]:
                raise FyersImportError("NSE internal identity changed")
            comparisons = tuple(
                _reconcile(
                    canonical, identity, candle.trade_date,
                    {"open_paise": candle.open_paise,
                     "high_paise": candle.high_paise,
                     "low_paise": candle.low_paise,
                     "close_paise": candle.close_paise,
                     "volume": candle.volume},
                    nse_references.get((identity.symbol, candle.trade_date)),
                ) for candle in batch.candles
            )
        if prior is not None:
            if prior[0] != batch.response_sha256:
                raise FyersImportError("provisional checkpoint hash differs")
            return ProvisionalResult(
                identity.symbol, len(batch.candles), False, True, True, missing,
                tuple(sorted(expected_days - identity.nse_evidence_dates)),
                comparisons, batch.response_sha256,
            )
        if not resumed:
            _write_raw(path, batch, expected_raw)
        staging.execute("BEGIN IMMEDIATE")
        cursor = staging.execute(
            "INSERT INTO provisional_runs (source_ref,response_sha256,"
            "retrieved_at_utc,symbol,ticker,isin,internal_id,master_sha256,"
            "first_date,last_date,identity_state) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (source_ref, batch.response_sha256,
             batch.retrieved_at_utc.isoformat(), identity.symbol, identity.ticker,
             identity.isin, identity.internal_id, identity.master_sha256,
             first.isoformat(), last.isoformat(), IdentityState.PROVISIONAL.value),
        )
        run_id = cursor.lastrowid
        for candle, comparison in zip(batch.candles, comparisons, strict=True):
            raw_payload = json.dumps(
                dict(candle.raw_payload), sort_keys=True, separators=(",", ":")
            )
            existing = staging.execute(
                "SELECT c.open_paise,c.high_paise,c.low_paise,c.close_paise,"
                "c.volume FROM provisional_candles c "
                "JOIN provisional_runs r ON r.id=c.run_id "
                "WHERE r.symbol=? AND c.trade_date=?",
                (identity.symbol, candle.trade_date.isoformat()),
            ).fetchall()
            facts = (candle.open_paise, candle.high_paise, candle.low_paise,
                     candle.close_paise, candle.volume)
            if existing:
                if len(existing) != 1 or existing[0] != facts:
                    raise FyersImportError("conflicting provisional duplicate")
                continue
            staging.execute(
                "INSERT INTO provisional_candles (run_id,source_record_key,"
                "trade_date,event_at_utc,payload_sha256,open_paise,high_paise,"
                "low_paise,close_paise,volume,reconciliation_state,"
                "nse_identity_evidence,differences_json) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, candle.source_record_key, candle.trade_date.isoformat(),
                 candle.start_utc.isoformat(),
                 hashlib.sha256(raw_payload.encode()).hexdigest(),
                 *facts, comparison.state, int(comparison.nse_identity_evidence),
                 json.dumps(dict(comparison.differences), sort_keys=True)),
            )
        staging.commit()
        return ProvisionalResult(
            identity.symbol, len(batch.candles), not resumed, resumed, False,
            missing, tuple(sorted(expected_days - identity.nse_evidence_dates)),
            comparisons, batch.response_sha256,
        )
