"""Build and populate a five-instrument, disposable Upstox research pilot."""

import gzip
import io
import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping
from urllib.error import URLError
from urllib.request import Request, urlopen

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.db.models  # noqa: F401 - register every table with Base.metadata
from app.db.base import Base
from app.db.models.prices import IngestionRun, Instrument, InstrumentMapping
from app.services.integrity_service import check_ledger_integrity
from app.services.market_data_service import (
    ObservationInput,
    add_verified_mapping,
    record_mapped_observation,
    resolve_instrument,
)
from app.services.upstox_historical_provider import PROVIDER, HistoricalBatch

PILOT_SYMBOLS = ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK")
MIN_COMMON_SESSIONS = 5
MAX_COMMON_SESSIONS = 20
MAX_CALENDAR_DAYS = 31
_UPSTOX_KEY = re.compile(r"NSE_EQ\|[^\s/\\?#]{1,248}\Z")
OFFICIAL_NSE_MASTER = (
    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
)


class PilotError(ValueError):
    """A reference subset, mapping, or observation failed closed."""


@dataclass(frozen=True)
class ReferenceInstrument:
    instrument_id: int
    symbol: str
    isin: str
    dates: tuple[date, ...]


@dataclass(frozen=True)
class PilotReference:
    database_path: Path
    dates: tuple[date, ...]
    instruments: tuple[ReferenceInstrument, ...]
    expected_nse_rows: int

    @property
    def instrument_ids(self) -> dict[str, int]:
        return {item.symbol: item.instrument_id for item in self.instruments}

    @property
    def from_date(self) -> date:
        return self.dates[0]

    @property
    def to_date(self) -> date:
        return self.dates[-1]


def _as_id(value: object) -> int:
    if type(value) is not int:
        raise PilotError("reference database contains an invalid ID")
    return value


def validate_temporary_target(path: Path) -> Path:
    target = path.expanduser().resolve()
    temp_roots = (Path(tempfile.gettempdir()).resolve(), Path("/private/tmp"))
    if not any(target.is_relative_to(root) for root in temp_roots):
        raise PilotError("pilot database must be in a temporary directory")
    if target.exists() or not target.parent.is_dir():
        raise PilotError("pilot database path must be new with an existing parent")
    return target


def _fetch_rows(
    connection: sqlite3.Connection, table: str, ids: set[int]
) -> list[dict[str, object]]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"SELECT * FROM {table} WHERE id IN ({placeholders}) ORDER BY id",  # noqa: S608
        tuple(sorted(ids)),
    )
    return [dict(row) for row in rows]


def _insert_rows(
    connection: sqlite3.Connection, table: str, rows: list[dict[str, object]]
) -> None:
    if not rows:
        return
    columns = tuple(rows[0])
    placeholders = ",".join("?" for _ in columns)
    names = ",".join(f'"{column}"' for column in columns)
    connection.executemany(
        f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
        [tuple(row[column] for column in columns) for row in rows],
    )


def _install_immutable_triggers(connection: sqlite3.Connection) -> None:
    for table, label in (
        ("source_observations", "source observations"),
        ("ingestion_runs", "ingestion runs"),
    ):
        for action in ("UPDATE", "DELETE"):
            connection.execute(
                f"CREATE TRIGGER {table}_no_{action.lower()} "
                f"BEFORE {action} ON {table} "
                f"BEGIN SELECT RAISE(ABORT, '{label} are immutable'); END"
            )


def prepare_reference_subset(
    reference_database: Path,
    target_database: Path,
    symbols: tuple[str, ...] = PILOT_SYMBOLS,
) -> PilotReference:
    """Copy only selected NSE facts; open the original SQLite file read-only."""
    target = validate_temporary_target(target_database)
    source = reference_database.expanduser().resolve()
    if not source.is_file():
        raise PilotError("reference database does not exist")
    if (
        len(symbols) != 5
        or len(set(symbols)) != 5
        or any(not re.fullmatch(r"[A-Z0-9]{1,20}", symbol) for symbol in symbols)
    ):
        raise PilotError("pilot requires exactly five distinct EQ symbols")

    try:
        with sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True) as original:
            original.row_factory = sqlite3.Row
            original.execute("PRAGMA query_only=ON")
            original.execute("BEGIN")
            revision = original.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            if revision is None or revision[0] != "0004_market_data_foundation":
                raise PilotError("reference database requires Phase 6A migration")

            instrument_rows: list[dict[str, object]] = []
            dates_by_symbol: dict[str, set[date]] = {}
            for symbol in symbols:
                matches = original.execute(
                    "SELECT * FROM instruments WHERE symbol=? AND series='EQ'",
                    (symbol,),
                ).fetchall()
                if len(matches) != 1 or not matches[0]["isin"]:
                    raise PilotError(f"{symbol} has missing or ambiguous NSE identity")
                instrument = dict(matches[0])
                instrument_rows.append(instrument)
                days = {
                    date.fromisoformat(row[0])
                    for row in original.execute(
                        """SELECT p.trade_date FROM daily_prices p
                           JOIN source_observations o ON o.id=p.source_observation_id
                           WHERE p.instrument_id=? AND p.import_batch_id IS NOT NULL
                             AND o.provider='nse_zip'""",
                        (instrument["id"],),
                    )
                }
                dates_by_symbol[symbol] = days
            common = sorted(set.intersection(*(dates_by_symbol[s] for s in symbols)))
            selected = common[-MAX_COMMON_SESSIONS:]
            while (
                selected and (selected[-1] - selected[0]).days + 1 > MAX_CALENDAR_DAYS
            ):
                selected.pop(0)
            if len(selected) < MIN_COMMON_SESSIONS:
                raise PilotError("fewer than five common NSE trading sessions")
            dates = tuple(selected)
            ids = {_as_id(item["id"]) for item in instrument_rows}
            day_values = tuple(day.isoformat() for day in dates)
            id_marks = ",".join("?" for _ in ids)
            day_marks = ",".join("?" for _ in dates)
            price_rows = [
                dict(row)
                for row in original.execute(
                    "SELECT * FROM daily_prices "
                    f"WHERE instrument_id IN ({id_marks}) "
                    f"AND trade_date IN ({day_marks}) ORDER BY id",
                    (*sorted(ids), *day_values),
                )
            ]
            if len(price_rows) != len(symbols) * len(dates) or any(
                row["source_observation_id"] is None for row in price_rows
            ):
                raise PilotError("selected NSE coverage or provenance is incomplete")
            batch_ids = {_as_id(row["import_batch_id"]) for row in price_rows}
            obs_ids = {_as_id(row["source_observation_id"]) for row in price_rows}
            observations = _fetch_rows(original, "source_observations", obs_ids)
            if len(observations) != len(price_rows):
                raise PilotError("selected NSE source observations are missing")
            run_ids = {_as_id(row["ingestion_run_id"]) for row in observations}
            runs = _fetch_rows(original, "ingestion_runs", run_ids)
            batches = _fetch_rows(original, "import_batches", batch_ids)
            mappings = [
                dict(row)
                for row in original.execute(
                    "SELECT * FROM instrument_mappings WHERE instrument_id IN "
                    f"({id_marks}) "
                    "AND provider='nse_zip' ORDER BY id",
                    tuple(sorted(ids)),
                )
            ]
            if len(runs) != len(run_ids) or len(batches) != len(batch_ids):
                raise PilotError("selected NSE import provenance is incomplete")
            for batch in batches:
                selected_count = sum(
                    row["import_batch_id"] == batch["id"] for row in price_rows
                )
                batch.update(
                    total_rows=selected_count,
                    imported_rows=selected_count,
                    skipped_rows=0,
                    rejected_rows=0,
                    status="completed",
                )
            original.rollback()
    except sqlite3.DatabaseError as exc:
        raise PilotError("reference database could not be read") from exc

    descriptor = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    engine = create_engine(f"sqlite:///{target}")
    try:
        Base.metadata.create_all(engine)
        engine.dispose()
        with sqlite3.connect(target) as copied:
            copied.execute("PRAGMA foreign_keys=ON")
            with copied:
                copied.execute(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
                copied.execute(
                    "INSERT INTO alembic_version (version_num) VALUES (?)",
                    ("0004_market_data_foundation",),
                )
                _insert_rows(copied, "instruments", instrument_rows)
                _insert_rows(copied, "import_batches", batches)
                _insert_rows(copied, "ingestion_runs", runs)
                _insert_rows(copied, "instrument_mappings", mappings)
                _insert_rows(copied, "source_observations", observations)
                _insert_rows(copied, "daily_prices", price_rows)
                _install_immutable_triggers(copied)
        integrity = check_ledger_integrity(target)
        if not integrity.is_valid:
            raise PilotError("reference subset failed integrity checks")
    except Exception:
        engine.dispose()
        target.unlink(missing_ok=True)
        raise
    references = tuple(
        ReferenceInstrument(
            instrument_id=_as_id(item["id"]),
            symbol=str(item["symbol"]),
            isin=str(item["isin"]),
            dates=dates,
        )
        for item in instrument_rows
    )
    return PilotReference(target, dates, references, len(price_rows))


def download_official_nse_master(
    reference: PilotReference,
) -> dict[str, dict[str, object]]:
    """Read one official NSE master and require one exact equity identity each."""
    try:
        request = Request(OFFICIAL_NSE_MASTER, headers={"Accept": "application/gzip"})
        with urlopen(request, timeout=20) as response:
            compressed = response.read(30_000_001)
        if len(compressed) > 30_000_000:
            raise PilotError("Upstox instrument master exceeds size limit")
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as archive:
            raw = archive.read(100_000_001)
        if len(raw) > 100_000_000:
            raise PilotError("Upstox instrument master exceeds size limit")
        records = json.loads(raw)
    except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        raise PilotError("official Upstox instrument master is unavailable") from None
    if not isinstance(records, list):
        raise PilotError("official Upstox instrument master has invalid structure")
    selected: dict[str, dict[str, object]] = {}
    for instrument in reference.instruments:
        candidates = [
            record for record in records
            if isinstance(record, dict)
            and record.get("trading_symbol") == instrument.symbol
            and record.get("exchange") == "NSE"
            and record.get("segment") == "NSE_EQ"
            and record.get("instrument_type") == "EQ"
        ]
        if len(candidates) != 1 or candidates[0].get("isin") != instrument.isin:
            raise PilotError(f"{instrument.symbol} official identity is ambiguous")
        record = dict(candidates[0])
        record["source"] = "official_upstox_nse_master"
        selected[instrument.symbol] = record
    return selected


def verify_mock_metadata(
    session: Session,
    metadata: Mapping[str, object],
    reference: PilotReference,
    symbol: str,
) -> InstrumentMapping:
    """Approve a bounded mock mapping only after full pilot identity checks."""
    if metadata.get("source") != "mock_instrument_master":
        raise PilotError(f"{symbol} mock instrument metadata failed verification")
    return _verify_metadata(session, metadata, reference, symbol, official=False)


def verify_official_metadata(
    session: Session,
    metadata: Mapping[str, object],
    reference: PilotReference,
    symbol: str,
) -> InstrumentMapping:
    """Match the official NSE master record on symbol, segment, type and ISIN."""
    if metadata.get("source") != "official_upstox_nse_master":
        raise PilotError(f"{symbol} official instrument metadata is missing")
    return _verify_metadata(session, metadata, reference, symbol, official=True)


def _verify_metadata(
    session: Session,
    metadata: Mapping[str, object],
    reference: PilotReference,
    symbol: str,
    *,
    official: bool,
) -> InstrumentMapping:
    instrument_id = reference.instrument_ids.get(symbol)
    if instrument_id is None:
        raise PilotError("symbol is outside the pilot universe")
    expected = next(item for item in reference.instruments if item.symbol == symbol)
    key = metadata.get("instrument_key")
    if (
        metadata.get("exchange") != "NSE"
        or metadata.get("segment") != "NSE_EQ"
        or metadata.get("instrument_type") != "EQ"
        or metadata.get("trading_symbol") != symbol
        or metadata.get("isin") != expected.isin
        or not isinstance(key, str)
        or _UPSTOX_KEY.fullmatch(key) is None
        or (official and key != f"NSE_EQ|{expected.isin}")
    ):
        raise PilotError(f"{symbol} instrument metadata failed verification")
    with session.begin():
        instrument = session.get(Instrument, instrument_id)
        if (
            instrument is None
            or instrument.symbol != symbol
            or instrument.series != "EQ"
            or instrument.isin != expected.isin
        ):
            raise PilotError(f"{symbol} stable instrument identity changed")
        candidates = list(
            session.scalars(
                select(InstrumentMapping).where(
                    InstrumentMapping.provider == PROVIDER,
                    InstrumentMapping.provider_instrument_key == key,
                    InstrumentMapping.symbol == symbol,
                    InstrumentMapping.exchange == "NSE",
                    InstrumentMapping.series == "EQ",
                    InstrumentMapping.valid_from == reference.from_date,
                    InstrumentMapping.valid_to == reference.to_date,
                    InstrumentMapping.identity_status == "verified",
                )
            )
        )
        if len(candidates) > 1:
            raise PilotError(f"{symbol} has ambiguous Upstox mappings")
        if candidates:
            return candidates[0]
        return add_verified_mapping(
            session,
            instrument_id=instrument_id,
            provider=PROVIDER,
            provider_instrument_key=key,
            symbol=symbol,
            exchange="NSE",
            series="EQ",
            valid_from=reference.from_date,
            valid_to=reference.to_date,
        )


def ingest_batch(
    session: Session,
    batch: HistoricalBatch,
    symbol: str,
    reference: PilotReference,
) -> int:
    """Append Upstox observations atomically; leave NSE canonical bars intact."""
    expected_id = reference.instrument_ids.get(symbol)
    if expected_id is None:
        raise PilotError("symbol is outside the pilot universe")
    if (batch.from_date, batch.to_date) != (reference.from_date, reference.to_date):
        raise PilotError("historical batch dates differ from the selected NSE range")
    source_ref = (
        f"{batch.source_ref} [{batch.instrument_key} "
        f"{batch.from_date}..{batch.to_date}]"
    )
    if not 1 <= len(source_ref) <= 512 or len(batch.response_sha256) != 64:
        raise PilotError("historical batch provenance is invalid")
    with session.begin():
        resolved = resolve_instrument(
            session,
            provider=PROVIDER,
            provider_instrument_key=batch.instrument_key,
            symbol=symbol,
            exchange="NSE",
            series="EQ",
            trade_date=reference.from_date,
        )
        if resolved.id != expected_id:
            raise PilotError("Upstox mapping points to a different instrument")
        existing_runs = list(
            session.scalars(
                select(IngestionRun).where(
                    IngestionRun.provider == PROVIDER,
                    IngestionRun.source_kind == "provider",
                    IngestionRun.source_ref == source_ref,
                    IngestionRun.source_sha256 == batch.response_sha256,
                )
            )
        )
        if len(existing_runs) > 1:
            raise PilotError("duplicate Upstox ingestion runs are ambiguous")
        if existing_runs:
            run = existing_runs[0]
        else:
            run = IngestionRun(
                provider=PROVIDER,
                source_kind="provider",
                source_ref=source_ref,
                source_sha256=batch.response_sha256,
                provenance_kind="source",
            )
            session.add(run)
            session.flush()
        created = 0
        for candle in batch.candles:
            if not reference.from_date <= candle.trade_date <= reference.to_date:
                raise PilotError("Upstox candle is outside the pilot date range")
            _, was_created = record_mapped_observation(
                session,
                ingestion_run=run,
                value=ObservationInput(
                    provider=PROVIDER,
                    source_record_key=(
                        f"{batch.instrument_key}:{candle.trade_date.isoformat()}"
                    ),
                    provider_instrument_key=batch.instrument_key,
                    symbol=symbol,
                    exchange="NSE",
                    series="EQ",
                    trade_date=candle.trade_date,
                    open_price=candle.open_price,
                    high_price=candle.high_price,
                    low_price=candle.low_price,
                    close_price=candle.close_price,
                    volume=candle.volume,
                    previous_close_price=None,
                    # V3's daily timestamp labels a bar; publication time is unknown.
                    event_at_utc=None,
                    retrieved_at_utc=batch.retrieved_at_utc,
                    available_at_utc=None,
                ),
                raw_payload=candle.raw_payload,
            )
            created += int(was_created)
        return created
