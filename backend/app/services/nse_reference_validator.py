"""Read-only reconciliation of operator-supplied NSE ZIPs with provisional FYERS."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urlparse

from app.modules.research.readiness_io import load_calendar_evidence
from app.services.fyers_import_service import _expected_days
from app.services.fyers_provisional_service import (
    NseReference,
    ProvisionalIdentity,
    _reconcile,
)
from app.services.price_import_service import (
    PriceImportError,
    _file_date,
    _parse_archive,
)

MAX_ZIP_BYTES = 50 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class ReferenceValidationError(ValueError):
    """Invalid offline inputs or unsafe database state."""


@dataclass(frozen=True)
class SourceSpec:
    day: date
    filename: str
    sha256: str


@dataclass(frozen=True)
class Comparison:
    day: str
    symbol: str
    status: str
    differences: dict[str, int]


@dataclass(frozen=True)
class FileCheck:
    day: str
    filename: str
    sha256: str | None
    status: str


@dataclass(frozen=True)
class ReferenceReport:
    files: tuple[FileCheck, ...]
    comparisons: tuple[Comparison, ...]
    issues: tuple[str, ...]
    canonical_sha256: str
    staging_sha256: str
    database_hashes_unchanged: bool

    @property
    def ready_for_reference_review(self) -> bool:
        return (
            not self.issues
            and self.database_hashes_unchanged
            and all(item.status == "VALID" for item in self.files)
            and all(item.status == "MATCH" for item in self.comparisons)
        )

    def summary(self) -> dict[str, object]:
        counts: dict[str, int] = {}
        for comparison in self.comparisons:
            counts[comparison.status] = counts.get(comparison.status, 0) + 1
        return {
            "ready_for_reference_review": self.ready_for_reference_review,
            "validated_files": sum(item.status == "VALID" for item in self.files),
            "expected_files": len(self.files),
            "comparison_counts": counts,
            "missing_dates": [
                item.day for item in self.files if item.status == "MISSING"
            ],
            "files": [asdict(item) for item in self.files],
            "discrepancies": [
                asdict(item)
                for item in self.comparisons
                if item.status not in {"MATCH", "NO_NSE_REFERENCE"}
            ],
            "issues": list(self.issues),
            "canonical_sha256": self.canonical_sha256,
            "staging_sha256": self.staging_sha256,
            "database_hashes_unchanged": self.database_hashes_unchanged,
            "identity_state": "PROVISIONAL",
            "research_eligible": False,
        }


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        if path.is_symlink() or path.stat().st_size > 2_000_000:
            raise ReferenceValidationError(f"{label} must be a small regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ReferenceValidationError(f"{label} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise ReferenceValidationError(f"{label} must be a JSON object")
    return value


def _source(item: object, *, supplemental: bool) -> SourceSpec:
    if not isinstance(item, dict):
        raise ReferenceValidationError("NSE source record must be an object")
    required = {"trade_date", "filename", "sha256"}
    if supplemental:
        required |= {"source_url", "acquired_at_utc"}
    if not required <= item.keys() or not all(
        isinstance(item[key], str) for key in required
    ):
        raise ReferenceValidationError("NSE source record lacks required fields")
    if supplemental and set(item) != required:
        raise ReferenceValidationError("supplemental source has unexpected fields")
    try:
        day = date.fromisoformat(item["trade_date"])
        if _file_date(item["filename"]) != day:
            raise ReferenceValidationError("NSE source filename/date mismatch")
    except (ValueError, PriceImportError) as exc:
        raise ReferenceValidationError(
            "NSE source date or filename is invalid"
        ) from exc
    if not SHA256.fullmatch(item["sha256"]):
        raise ReferenceValidationError("NSE source SHA-256 is invalid")
    if supplemental:
        url = urlparse(item["source_url"])
        try:
            acquired = datetime.fromisoformat(item["acquired_at_utc"])
        except ValueError as exc:
            raise ReferenceValidationError("acquisition time is invalid") from exc
        if (
            url.scheme != "https"
            or url.hostname not in {"www.nseindia.com", "nsearchives.nseindia.com"}
            or url.username is not None
            or url.password is not None
            or acquired.utcoffset() != UTC.utcoffset(None)
        ):
            raise ReferenceValidationError("NSE source provenance is invalid")
    return SourceSpec(day, item["filename"], item["sha256"])


def _sources(
    evidence: dict[str, object], supplement: Path | None, expected: set[date]
) -> dict[date, SourceSpec]:
    original = evidence.get("nse_sources")
    if not isinstance(original, list):
        raise ReferenceValidationError("NSE evidence lacks source records")
    entries = [_source(item, supplemental=False) for item in original]
    if supplement is not None:
        extra = _read_json(supplement, "supplemental manifest")
        if set(extra) != {"sources"} or not isinstance(extra["sources"], list):
            raise ReferenceValidationError("supplemental manifest needs sources")
        entries.extend(_source(item, supplemental=True) for item in extra["sources"])
    result: dict[date, SourceSpec] = {}
    for entry in entries:
        if entry.day not in expected or entry.day in result:
            raise ReferenceValidationError("duplicate or out-of-window NSE source")
        result[entry.day] = entry
    return result


def _readonly_database(path: Path, required: set[str]) -> sqlite3.Connection:
    try:
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
            raise ReferenceValidationError("database must be a regular file")
        connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        connection.execute("PRAGMA query_only=ON")
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if (
            not required <= tables
            or connection.execute("PRAGMA integrity_check").fetchone() != ("ok",)
            or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
        ):
            connection.close()
            raise ReferenceValidationError("database schema or integrity failed")
        return connection
    except (OSError, sqlite3.Error) as exc:
        raise ReferenceValidationError("database is unavailable or invalid") from exc


def validate_references(
    *,
    canonical_path: Path,
    staging_path: Path,
    calendar_path: Path,
    evidence_path: Path,
    reference_dirs: tuple[Path, ...],
    supplemental_manifest: Path | None = None,
    first: date = date(2026, 10, 12),
    last: date = date(2026, 11, 6),
    symbols: tuple[str, ...] = ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"),
) -> ReferenceReport:
    """Validate local ZIPs and compare staged bars; never request or write data."""
    if not reference_dirs or canonical_path.resolve() == staging_path.resolve():
        raise ReferenceValidationError(
            "separate databases and reference directories required"
        )
    calendar = load_calendar_evidence(calendar_path)
    days = _expected_days(calendar, first, last)
    evidence = _read_json(evidence_path, "historical evidence")
    instruments = evidence.get("instruments")
    if not isinstance(instruments, dict) or not set(symbols) <= set(instruments):
        raise ReferenceValidationError("five-stock identity evidence is incomplete")
    sources = _sources(evidence, supplemental_manifest, set(days))
    for directory in reference_dirs:
        if directory.is_symlink() or not directory.is_dir():
            raise ReferenceValidationError("reference directory is unavailable")
    before = (
        hashlib.sha256(canonical_path.read_bytes()).hexdigest(),
        hashlib.sha256(staging_path.read_bytes()).hexdigest(),
    )
    canonical = _readonly_database(canonical_path, {"instruments", "daily_prices"})
    staging = _readonly_database(
        staging_path, {"provisional_runs", "provisional_candles"}
    )
    files: list[FileCheck] = []
    comparisons: list[Comparison] = []
    issues: list[str] = []
    try:
        identities: dict[str, ProvisionalIdentity] = {}
        staged: dict[tuple[str, date], dict[str, int]] = {}
        for symbol in symbols:
            item = instruments[symbol]
            if not isinstance(item, dict) or not isinstance(item.get("isin"), str):
                raise ReferenceValidationError("instrument evidence is invalid")
            isin = item["isin"]
            internal = canonical.execute(
                "SELECT id FROM instruments WHERE symbol=? AND series='EQ' AND isin=?",
                (symbol, isin),
            ).fetchall()
            run = staging.execute(
                "SELECT id,internal_id FROM provisional_runs WHERE symbol=? "
                "AND isin=? AND identity_state='PROVISIONAL'",
                (symbol, isin),
            ).fetchall()
            if len(internal) != 1 or len(run) != 1 or internal[0][0] != run[0][1]:
                raise ReferenceValidationError("staged and canonical identity mismatch")
            identities[symbol] = ProvisionalIdentity(
                symbol,
                f"NSE:{symbol}-EQ",
                isin,
                internal[0][0],
                frozenset(days),
                "",  # NSE evidence is assessed per file below.
            )
            for row in staging.execute(
                "SELECT c.trade_date,c.open_paise,c.high_paise,c.low_paise,"
                "c.close_paise,c.volume FROM provisional_candles c "
                "WHERE c.run_id=?",
                (run[0][0],),
            ):
                key = (symbol, date.fromisoformat(row[0]))
                if key in staged or key[1] not in days:
                    raise ReferenceValidationError(
                        "duplicate or out-of-window staged candle"
                    )
                staged[key] = dict(
                    zip(
                        (
                            "open_paise",
                            "high_paise",
                            "low_paise",
                            "close_paise",
                            "volume",
                        ),
                        row[1:],
                        strict=True,
                    )
                )
        if len(staged) != len(symbols) * len(days):
            raise ReferenceValidationError("provisional candle coverage is incomplete")
        for day in days:
            spec = sources.get(day)
            name = (
                spec.filename
                if spec
                else f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
            )
            paths = [
                directory / name
                for directory in reference_dirs
                if (directory / name).exists() or (directory / name).is_symlink()
            ]
            if len(paths) > 1:
                issues.append(f"{day}: duplicate local ZIP paths")
                files.append(FileCheck(day.isoformat(), name, None, "INVALID"))
                comparisons.extend(
                    Comparison(day.isoformat(), symbol, "INVALID_REFERENCE", {})
                    for symbol in symbols
                )
                continue
            if spec is None or not paths:
                if spec is None and paths:
                    issues.append(f"{day}: local ZIP has no hash/provenance record")
                elif spec is not None:
                    issues.append(f"{day}: manifest ZIP is missing")
                files.append(FileCheck(day.isoformat(), name, None, "MISSING"))
                comparisons.extend(
                    Comparison(day.isoformat(), symbol, "NO_NSE_REFERENCE", {})
                    for symbol in symbols
                )
                continue
            path = paths[0]
            try:
                metadata = path.lstat()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size > MAX_ZIP_BYTES
                ):
                    raise PriceImportError("ZIP is not a bounded regular file")
                content = path.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                if digest != spec.sha256:
                    raise PriceImportError("ZIP SHA-256 differs from manifest")
                rows, _, rejected, _ = _parse_archive(
                    content,
                    name,
                    day,
                    MAX_ZIP_BYTES,
                    False,
                )
                if rejected:
                    raise PriceImportError("ZIP has rejected rows")
                target = {row.symbol: row for row in rows if row.symbol in symbols}
                if set(target) != set(symbols):
                    raise PriceImportError("one or more pilot EQ symbols are absent")
            except (OSError, PriceImportError) as exc:
                issues.append(f"{day}: invalid ZIP: {exc}")
                files.append(FileCheck(day.isoformat(), name, None, "INVALID"))
                comparisons.extend(
                    Comparison(day.isoformat(), symbol, "INVALID_REFERENCE", {})
                    for symbol in symbols
                )
                continue
            files.append(FileCheck(day.isoformat(), name, digest, "VALID"))
            for symbol in symbols:
                row = target[symbol]
                identity = identities[symbol]
                if row.isin != identity.isin:
                    issues.append(
                        f"{day}: {symbol} NSE ISIN conflicts with reviewed identity"
                    )
                    comparisons.append(
                        Comparison(day.isoformat(), symbol, "ISIN_CONFLICT", {})
                    )
                    continue
                reference = NseReference(
                    symbol,
                    row.isin,
                    day,
                    row.open_paise,
                    row.high_paise,
                    row.low_paise,
                    row.close_paise,
                    row.volume,
                )
                try:
                    result = _reconcile(
                        canonical,
                        identity,
                        day,
                        staged[(symbol, day)],
                        reference,
                    )
                except ValueError:
                    issues.append(f"{day}: canonical/local NSE conflict for {symbol}")
                    comparisons.append(
                        Comparison(day.isoformat(), symbol, "CANONICAL_CONFLICT", {})
                    )
                    continue
                comparisons.append(
                    Comparison(
                        day.isoformat(), symbol, result.state, dict(result.differences)
                    )
                )
    finally:
        staging.close()
        canonical.close()
    after = (
        hashlib.sha256(canonical_path.read_bytes()).hexdigest(),
        hashlib.sha256(staging_path.read_bytes()).hexdigest(),
    )
    if after != before:
        issues.append("source database bytes changed during validation")
    return ReferenceReport(
        tuple(files),
        tuple(comparisons),
        tuple(issues),
        before[0],
        before[1],
        before == after,
    )
