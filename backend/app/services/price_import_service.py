"""Strict, offline import of the observed NSE CM bhavcopy ZIP profile."""

import csv
import hashlib
import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.prices import (
    DailyPrice,
    ImportBatch,
    IngestionRun,
    Instrument,
    InstrumentMapping,
)
from app.services.market_data_service import NormalizedObservation, record_observation

HEADERS = (
    "TradDt",
    "BizDt",
    "Sgmt",
    "Src",
    "FinInstrmTp",
    "FinInstrmId",
    "ISIN",
    "TckrSymb",
    "SctySrs",
    "XpryDt",
    "FininstrmActlXpryDt",
    "StrkPric",
    "OptnTp",
    "FinInstrmNm",
    "OpnPric",
    "HghPric",
    "LwPric",
    "ClsPric",
    "LastPric",
    "PrvsClsgPric",
    "UndrlygPric",
    "SttlmPric",
    "OpnIntrst",
    "ChngInOpnIntrst",
    "TtlTradgVol",
    "TtlTrfVal",
    "TtlNbOfTxsExctd",
    "SsnId",
    "NewBrdLotQty",
    "Rmks",
    "Rsvd1",
    "Rsvd2",
    "Rsvd3",
    "Rsvd4",
)
FILE_PATTERN = re.compile(r"BhavCopy_NSE_CM_0_0_0_(\d{8})_F_0000\.csv\.zip\Z")
PRICE_COLUMNS = {
    "OpnPric": "open_paise",
    "HghPric": "high_paise",
    "LwPric": "low_paise",
    "ClsPric": "close_paise",
    "PrvsClsgPric": "previous_close_paise",
}


class PriceImportError(ValueError):
    """Invalid file, row, or conflicting existing price data."""


@dataclass(frozen=True)
class RejectedRow:
    line: int
    reason: str


@dataclass(frozen=True)
class ImportResult:
    file_sha256: str
    already_imported: bool
    total_rows: int
    imported_rows: int
    skipped_rows: int
    unchanged_rows: int
    skipped_by_series: dict[str, int]
    rejected_rows: tuple[RejectedRow, ...]


@dataclass(frozen=True)
class _PriceRow:
    line: int
    symbol: str
    isin: str | None
    trade_date: date
    open_paise: int
    high_paise: int
    low_paise: int
    close_paise: int
    previous_close_paise: int
    volume: int
    provider_instrument_key: str | None
    raw_fields: dict[str, str]


def import_price_file(
    session: Session,
    path: Path,
    source_label: str,
    *,
    max_uncompressed_bytes: int = 50 * 1024 * 1024,
    skip_bad_rows: bool = False,
) -> ImportResult:
    """Validate one ZIP fully, then persist its prices in one transaction."""
    if not source_label.strip() or len(source_label.strip()) > 100:
        raise PriceImportError("source label must contain 1 to 100 characters")
    if max_uncompressed_bytes <= 0:
        raise PriceImportError("uncompressed size limit must be positive")
    file_date = _file_date(path.name)
    try:
        archive_bytes = path.read_bytes()
    except OSError as exc:
        raise PriceImportError(f"cannot read local file: {exc}") from exc
    file_hash = hashlib.sha256(archive_bytes).hexdigest()
    rows, skipped_series, rejected, total = _parse_archive(
        archive_bytes, path.name, file_date, max_uncompressed_bytes, skip_bad_rows
    )
    with session.begin():
        previous_batch = session.scalar(
            select(ImportBatch).where(ImportBatch.file_sha256 == file_hash)
        )
        if previous_batch is not None:
            return ImportResult(
                file_hash,
                True,
                previous_batch.total_rows,
                0,
                previous_batch.skipped_rows,
                0,
                skipped_series,
                (),
            )

        to_add: list[tuple[_PriceRow, Instrument]] = []
        observed: list[tuple[_PriceRow, Instrument, bool]] = []
        unchanged = 0
        instruments: dict[str, Instrument] = {}
        for row in rows:
            instrument = instruments.get(row.symbol)
            if instrument is None:
                instrument = session.scalar(
                    select(Instrument).where(
                        Instrument.symbol == row.symbol, Instrument.series == "EQ"
                    )
                )
                if instrument is None:
                    instrument = Instrument(
                        symbol=row.symbol, series="EQ", isin=row.isin
                    )
                    session.add(instrument)
                    session.flush()
                legacy_mapping = session.scalar(
                    select(InstrumentMapping).where(
                        InstrumentMapping.instrument_id == instrument.id,
                        InstrumentMapping.provider == "nse_zip",
                        InstrumentMapping.symbol == row.symbol,
                        InstrumentMapping.series == "EQ",
                    )
                )
                if legacy_mapping is None:
                    session.add(
                        InstrumentMapping(
                            instrument_id=instrument.id,
                            provider="nse_zip",
                            provider_instrument_key=None,
                            symbol=row.symbol,
                            exchange="NSE",
                            series="EQ",
                            valid_from=None,
                            valid_to=None,
                            identity_status="legacy_unverified",
                        )
                    )
                instruments[row.symbol] = instrument
            if instrument.isin != row.isin:
                raise PriceImportError(
                    f"line {row.line}: ISIN conflict for {row.symbol}"
                )
            existing = session.scalar(
                select(DailyPrice).where(
                    DailyPrice.instrument_id == instrument.id,
                    DailyPrice.trade_date == row.trade_date,
                )
            )
            if existing is not None:
                if _same_price(existing, row):
                    unchanged += 1
                    observed.append((row, instrument, False))
                    continue
                raise PriceImportError(
                    f"line {row.line}: price conflict for {row.symbol} "
                    f"on {row.trade_date.isoformat()}"
                )
            to_add.append((row, instrument))
            observed.append((row, instrument, True))

        skipped = sum(skipped_series.values()) + unchanged
        batch = ImportBatch(
            file_name=path.name,
            file_sha256=file_hash,
            source_label=source_label.strip(),
            total_rows=total,
            imported_rows=len(to_add),
            skipped_rows=skipped,
            rejected_rows=len(rejected),
            status="completed_with_skips" if skipped or rejected else "completed",
        )
        session.add(batch)
        session.flush()
        run = IngestionRun(
            provider="nse_zip",
            source_kind="zip",
            source_ref=path.name,
            source_sha256=file_hash,
            import_batch_id=batch.id,
            provenance_kind="source",
        )
        session.add(run)
        session.flush()
        for row, instrument, selected in observed:
            observation, _ = record_observation(
                session,
                ingestion_run=run,
                instrument_id=instrument.id,
                value=NormalizedObservation(
                    provider="nse_zip",
                    source_record_key=f"{file_hash}:{row.line}",
                    provider_instrument_key=row.provider_instrument_key,
                    symbol=row.symbol,
                    exchange="NSE",
                    series="EQ",
                    trade_date=row.trade_date,
                    open_paise=row.open_paise,
                    high_paise=row.high_paise,
                    low_paise=row.low_paise,
                    close_paise=row.close_paise,
                    previous_close_paise=row.previous_close_paise,
                    volume=row.volume,
                ),
                raw_payload=row.raw_fields,
                selected_for_canonical=selected,
            )
            if selected:
                session.add(
                    DailyPrice(
                        instrument_id=instrument.id,
                        import_batch_id=batch.id,
                        source_observation_id=observation.id,
                        trade_date=row.trade_date,
                        open_paise=row.open_paise,
                        high_paise=row.high_paise,
                        low_paise=row.low_paise,
                        close_paise=row.close_paise,
                        previous_close_paise=row.previous_close_paise,
                        volume=row.volume,
                        price_basis="unadjusted",
                    )
                )
        session.flush()
    return ImportResult(
        file_hash,
        False,
        total,
        len(to_add),
        skipped,
        unchanged,
        skipped_series,
        tuple(rejected),
    )


def _file_date(file_name: str) -> date:
    match = FILE_PATTERN.fullmatch(file_name)
    if match is None:
        raise PriceImportError(
            "file name does not match the supported bhavcopy profile"
        )
    try:
        return date.fromisoformat(f"{match[1][:4]}-{match[1][4:6]}-{match[1][6:]}")
    except ValueError as exc:
        raise PriceImportError("file name contains an invalid date") from exc


def _parse_archive(
    archive_bytes: bytes,
    file_name: str,
    file_date: date,
    limit: int,
    skip_bad_rows: bool,
) -> tuple[list[_PriceRow], dict[str, int], list[RejectedRow], int]:
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = archive.infolist()
            if len(members) != 1:
                raise PriceImportError("ZIP must contain exactly one CSV member")
            member = members[0]
            if "/" in member.filename or "\\" in member.filename:
                raise PriceImportError(
                    "ZIP member name must not contain path separators"
                )
            if member.filename != file_name.removesuffix(".zip"):
                raise PriceImportError("ZIP member name does not match file name")
            if member.file_size > limit:
                raise PriceImportError("CSV exceeds uncompressed size limit")
            with archive.open(member) as source:
                contents = source.read(limit + 1)
            if len(contents) > limit:
                raise PriceImportError("CSV exceeds uncompressed size limit")
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise PriceImportError(f"invalid ZIP archive: {exc}") from exc

    try:
        decoded = contents.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PriceImportError("CSV must be UTF-8") from exc
    reader = csv.DictReader(
        io.StringIO(decoded, newline=""),
        restkey="__extra__",
        restval=None,
        strict=True,
    )
    try:
        actual = reader.fieldnames
        if actual != list(HEADERS):
            missing = [
                header for header in HEADERS if actual is None or header not in actual
            ]
            unexpected = (
                []
                if actual is None
                else [header for header in actual if header not in HEADERS]
            )
            raise PriceImportError(
                f"CSV headers differ: missing={missing}; unexpected={unexpected}; "
                "exact order required"
            )
        rows: list[_PriceRow] = []
        rejected: list[RejectedRow] = []
        skipped_series: Counter[str] = Counter()
        seen: set[tuple[str, date]] = set()
        total = 0
        for record in reader:
            total += 1
            line = reader.line_num
            if "__extra__" in record or any(value is None for value in record.values()):
                _reject(line, "wrong number of columns", skip_bad_rows, rejected)
                continue
            _check_file_identity(record, file_date, line)
            series = record["SctySrs"]
            if series != "EQ":
                skipped_series[series or "<blank>"] += 1
                continue
            try:
                row = _parse_eq_row(record, file_date, line)
                key = (row.symbol, row.trade_date)
                if key in seen:
                    raise PriceImportError("duplicate symbol and date in file")
                seen.add(key)
                rows.append(row)
            except PriceImportError as exc:
                _reject(line, str(exc), skip_bad_rows, rejected)
        return rows, dict(skipped_series), rejected, total
    except csv.Error as exc:
        raise PriceImportError(f"invalid CSV: {exc}") from exc


def _reject(
    line: int, reason: str, skip_bad_rows: bool, rejected: list[RejectedRow]
) -> None:
    if not skip_bad_rows:
        raise PriceImportError(f"line {line}: {reason}")
    rejected.append(RejectedRow(line, reason))


def _check_file_identity(record: dict[str, str], file_date: date, line: int) -> None:
    expected = file_date.isoformat()
    for column in ("TradDt", "BizDt"):
        if record[column] != expected:
            raise PriceImportError(f"line {line}: {column} must equal {expected}")
    for column, expected_value in (
        ("Sgmt", "CM"),
        ("Src", "NSE"),
        ("FinInstrmTp", "STK"),
    ):
        if record[column] != expected_value:
            raise PriceImportError(f"line {line}: {column} must equal {expected_value}")


def _parse_eq_row(record: dict[str, str], trade_date: date, line: int) -> _PriceRow:
    symbol = record["TckrSymb"].strip().upper()
    if not symbol or len(symbol) > 20:
        raise PriceImportError("symbol must contain 1 to 20 characters")
    isin = record["ISIN"].strip().upper() or None
    if isin is not None and len(isin) != 12:
        raise PriceImportError("ISIN must contain 12 characters")
    prices = {
        attribute: _paise(record[column], column)
        for column, attribute in PRICE_COLUMNS.items()
    }
    low, high = prices["low_paise"], prices["high_paise"]
    if low > high:
        raise PriceImportError("low price exceeds high price")
    if not low <= prices["open_paise"] <= high:
        raise PriceImportError("open price is outside low-high range")
    if not low <= prices["close_paise"] <= high:
        raise PriceImportError("close price is outside low-high range")
    volume_text = record["TtlTradgVol"]
    if not volume_text.isascii() or not volume_text.isdecimal():
        raise PriceImportError("volume must be a non-negative integer")
    volume = int(volume_text)
    if volume > 2**63 - 1:
        raise PriceImportError("volume exceeds database integer range")
    return _PriceRow(
        line,
        symbol,
        isin,
        trade_date,
        volume=volume,
        provider_instrument_key=record["FinInstrmId"].strip() or None,
        raw_fields=dict(record),
        **prices,
    )


def _paise(raw: str, column: str) -> int:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise PriceImportError(f"{column} must be a decimal price") from exc
    if not value.is_finite() or value <= 0:
        raise PriceImportError(f"{column} must be positive and finite")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -2:
        raise PriceImportError(f"{column} has more than two decimal places")
    paise = int(value * 100)
    if paise > 2**63 - 1:
        raise PriceImportError(f"{column} exceeds database integer range")
    return paise


def _same_price(existing: DailyPrice, row: _PriceRow) -> bool:
    return (
        all(
            getattr(existing, column) == getattr(row, column)
            for column in (*PRICE_COLUMNS.values(), "volume")
        )
        and existing.price_basis == "unadjusted"
    )
