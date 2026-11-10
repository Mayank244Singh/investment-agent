"""Calendar-gated, provisional FYERS daily history collection.

This module never promotes observations or opens the canonical database for write.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping
from zoneinfo import ZoneInfo

from app.modules.research.readiness import CalendarEvidence
from app.services.fyers_historical_provider import (
    FyersError,
    FyersHistoricalDataProvider,
)
from app.services.fyers_import_service import FyersImportError, _expected_days
from app.services.fyers_provisional_service import (
    NseReference,
    ProvisionalIdentity,
    ProvisionalResult,
    load_local_nse_references,
    stage_fyers_daily_window,
    verify_nse_references,
)
from app.services.historical_transport import HttpResponse
from app.services.nse_reference_validator import _sources

DAILY_WINDOW_DAYS = 366
MAX_ATTEMPTS_PER_WINDOW = 3


@dataclass(frozen=True)
class UniverseEntry:
    symbol: str
    ticker: str
    isin: str
    evidence_ref: str


@dataclass(frozen=True)
class RequestWindow:
    symbol: str
    first: date
    last: date
    request_id: str


@dataclass(frozen=True)
class CollectionPlan:
    canonical: Path
    staging: Path
    raw_dir: Path
    entries: tuple[UniverseEntry, ...]
    identities: Mapping[str, ProvisionalIdentity]
    calendar: CalendarEvidence
    references: Mapping[tuple[str, date], NseReference]
    windows: tuple[RequestWindow, ...]
    master_sha256: str
    calendar_sha256: str
    universe_sha256: str


def date_windows(first: date, last: date) -> tuple[tuple[date, date], ...]:
    """Inclusive, contiguous windows no longer than FYERS' 366-day daily cap."""
    if first > last:
        raise FyersImportError("history start must not follow end")
    windows: list[tuple[date, date]] = []
    cursor = first
    while cursor <= last:
        end = min(cursor + timedelta(days=DAILY_WINDOW_DAYS - 1), last)
        windows.append((cursor, end))
        cursor = end + timedelta(days=1)
    return tuple(windows)


def load_universe(path: Path) -> tuple[UniverseEntry, ...]:
    if path.stat().st_size > 200_000:
        raise FyersImportError("universe file exceeds size limit")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(payload, dict) or set(payload) != {"version", "instruments"}
            or payload["version"] != 1 or not isinstance(payload["instruments"], list)
            or not 1 <= len(payload["instruments"]) <= 500):
        raise FyersImportError("universe requires version 1 and 1–500 instruments")
    result: list[UniverseEntry] = []
    seen: set[str] = set()
    for item in payload["instruments"]:
        if (not isinstance(item, dict)
                or set(item) != {"symbol", "ticker", "isin", "evidence_ref"}
                or not all(isinstance(v, str) for v in item.values())):
            raise FyersImportError("universe instrument fields are invalid")
        entry = UniverseEntry(**item)
        if (not entry.symbol or entry.symbol != entry.symbol.strip().upper()
                or entry.ticker != f"NSE:{entry.symbol}-EQ"
                or len(entry.isin) != 12 or not entry.evidence_ref.strip()
                or entry.symbol in seen):
            raise FyersImportError("universe identity is duplicate or invalid")
        seen.add(entry.symbol)
        result.append(entry)
    return tuple(result)


def _private_json(path: Path, label: str, limit: int) -> dict[str, object]:
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077 or metadata.st_nlink != 1
            or metadata.st_size > limit):
        raise FyersImportError(f"{label} must be a private regular file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FyersImportError(f"{label} must be an object")
    return value


def _readonly_staging(path: Path) -> None:
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077 or metadata.st_nlink != 1):
        raise FyersImportError("provisional database must be a private regular file")
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise FyersImportError("provisional database failed integrity check")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise FyersImportError("provisional database has foreign-key violations")
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if not {"provisional_runs", "provisional_candles"} <= tables:
            raise FyersImportError("provisional database lacks staging tables")


def prepare_plan(
    *, canonical: Path, staging: Path, raw_dir: Path, universe_file: Path,
    master_file: Path, evidence_file: Path, calendar_file: Path,
    nse_zip_dir: Path, supplemental_manifest: Path | None,
    first: date, last: date,
) -> CollectionPlan:
    """Read-only preflight; full reviewed calendar is mandatory."""
    if (canonical.resolve() == staging.resolve()
            or last >= datetime.now(ZoneInfo("Asia/Kolkata")).date()):
        raise FyersImportError("separate databases and completed past dates required")
    _readonly_staging(staging)
    from app.modules.research.readiness_io import load_calendar_evidence
    from app.services.fyers_import_cli import _require_migration

    _require_migration(canonical)
    calendar = load_calendar_evidence(calendar_file)
    spans = date_windows(first, last)
    # A five-year calendar must classify weekends too: a Sunday may be a
    # live Muhurat or Budget session rather than an ordinary closure.
    classified = {session.day for session in calendar.calendar.sessions}
    classified.update(calendar.calendar.holidays)
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        if day not in classified:
            raise FyersImportError(f"calendar does not classify {day}")
    for start, end in spans:
        _expected_days(calendar, start, end)
    entries = load_universe(universe_file)
    master = _private_json(master_file, "FYERS master", 50_000_000)
    evidence = _private_json(evidence_file, "identity evidence", 200_000)
    master_sha = hashlib.sha256(master_file.read_bytes()).hexdigest()
    current = evidence.get("fyers_current_master")
    reviewed = evidence.get("instruments")
    if (not isinstance(current, dict) or current.get("sha256") != master_sha
            or not isinstance(reviewed, dict)):
        raise FyersImportError("FYERS master and identity evidence differ")
    identities: dict[str, ProvisionalIdentity] = {}
    with sqlite3.connect(f"{canonical.as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        for entry in entries:
            record = master.get(entry.ticker)
            review = reviewed.get(entry.symbol)
            rows = connection.execute(
                "SELECT id,isin FROM instruments WHERE symbol=? AND series='EQ'",
                (entry.symbol,),
            ).fetchall()
            if (len(rows) != 1 or rows[0][1] != entry.isin
                    or not isinstance(record, dict)
                    or record.get("exSymbol") != entry.symbol
                    or record.get("symTicker") != entry.ticker
                    or record.get("exSeries") != "EQ"
                    or record.get("exchangeName") != "NSE"
                    or record.get("isin") != entry.isin
                    or not isinstance(review, dict)
                    or review.get("isin") != entry.isin
                    or review.get("fyers_current_ticker") != entry.ticker
                    or review.get("verified_mapping_created") is not False
                    or entry.evidence_ref != f"instruments.{entry.symbol}"):
                raise FyersImportError(
                    f"{entry.symbol}: current NSE/FYERS identity evidence differs"
                )
            identities[entry.symbol] = ProvisionalIdentity(
                entry.symbol, entry.ticker, entry.isin, rows[0][0],
                frozenset(), master_sha,
            )
    expected = {day for start, end in spans for day in _expected_days(
        calendar, start, end
    )}
    sources = _sources(evidence, supplemental_manifest, expected)
    dates = frozenset(sources)
    identities = {
        symbol: ProvisionalIdentity(
            item.symbol, item.ticker, item.isin, item.internal_id,
            dates, item.master_sha256,
        ) for symbol, item in identities.items()
    }
    references = load_local_nse_references(
        nse_zip_dir,
        [{"trade_date": source.day.isoformat(), "filename": source.filename,
          "sha256": source.sha256} for source in sources.values()],
        identities,
    )
    verify_nse_references(canonical, identities, references)
    windows = tuple(
        RequestWindow(entry.symbol, start, end, hashlib.sha256(
            f"fyers:D:{entry.ticker}:{start}:{end}:{master_sha}".encode()
        ).hexdigest())
        for entry in entries for start, end in spans
    )
    return CollectionPlan(
        canonical, staging, raw_dir, entries, identities, calendar,
        references, windows, master_sha,
        hashlib.sha256(calendar_file.read_bytes()).hexdigest(),
        hashlib.sha256(universe_file.read_bytes()).hexdigest(),
    )


def _snapshot_staging(plan: CollectionPlan) -> Path:
    """Consistent private SQLite backup before the first possible staging write."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = plan.raw_dir / f"pre_collection_{stamp}.db"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    os.close(descriptor)
    try:
        with sqlite3.connect(f"{plan.staging.as_uri()}?mode=ro", uri=True) as src:
            with sqlite3.connect(path) as dst:
                src.backup(dst)
                if dst.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise FyersImportError("pre-collection snapshot failed integrity")
        return path
    except (OSError, sqlite3.Error, FyersImportError):
        path.unlink(missing_ok=True)
        raise


def _staged_rows(path: Path) -> int:
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        return int(connection.execute(
            "SELECT count(*) FROM provisional_candles"
        ).fetchone()[0])


def _retryable_error(error: FyersError) -> bool:
    """HTTP 422 and other client errors never retry, even with a rate code."""
    if error.status_code is not None:
        return error.status_code in {429, 500, 502, 503, 504}
    return (error.provider_code in {-360, -429}
            or str(error) == "FYERS network request failed")


def collect(
    plan: CollectionPlan, provider: FyersHistoricalDataProvider, *,
    max_requests: int, resume: bool,
    sleeper: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Bound requests and retries; retain immutable raw/SQLite checkpoints."""
    if max_requests <= 0:
        raise FyersImportError("max requests must be positive")
    from app.services.fyers_provisional_service import _raw_path

    existing = []
    for window in plan.windows:
        identity = plan.identities[window.symbol]
        path = _raw_path(plan.raw_dir, identity, window.first, window.last)
        if path.exists():
            existing.append(path)
    if existing and not resume:
        raise FyersImportError("existing checkpoint requires --resume")
    snapshot = _snapshot_staging(plan)
    results: list[dict[str, object]] = []
    requests = 0
    failures: list[str] = []
    observed_dates: set[date] = set()
    missing_dates: set[str] = set()
    returned_candles = 0
    already_staged_or_overlap = 0
    conflicts = 0

    class MeteredTransport:
        def get(
            self, url: str, headers: Mapping[str, str],
            timeout_seconds: float, max_response_bytes: int,
        ) -> HttpResponse:
            nonlocal requests
            if requests >= max_requests:
                raise FyersImportError("request budget exhausted")
            if requests:
                sleeper(1.3)  # Below Standard's 5/s and 50/min.
            requests += 1
            return provider.transport.get(
                url, headers, timeout_seconds, max_response_bytes,
            )

    metered = FyersHistoricalDataProvider(
        transport=MeteredTransport(),
        app_id_getter=provider._app_id_getter,
        token_getter=provider._token_getter,
        clock=provider._clock,
    )
    for window in plan.windows:
        identity = plan.identities[window.symbol]
        raw_path = _raw_path(plan.raw_dir, identity, window.first, window.last)
        if requests >= max_requests and not raw_path.exists():
            failures.append("request budget exhausted; use --resume after review")
            break
        result: ProvisionalResult | None = None
        rows_before = _staged_rows(plan.staging)
        for attempt in range(MAX_ATTEMPTS_PER_WINDOW):
            if requests >= max_requests and not raw_path.exists():
                break
            before = requests
            try:
                result = stage_fyers_daily_window(
                    plan.canonical, plan.staging, metered,
                    identity=identity, first=window.first, last=window.last,
                    calendar=plan.calendar, raw_dir=plan.raw_dir,
                    nse_references=plan.references, max_window_days=366,
                )
                break
            except FyersError as exc:
                transient = _retryable_error(exc)
                if not transient or requests == before or attempt == 2:
                    failures.append(f"{window.symbol} {window.first}: {exc}")
                    if exc.status_code == 401 or exc.provider_code in {
                        -8, -15, -16, -17,
                    }:
                        failures.append(
                            "Refresh the FYERS access token manually with the "
                            "existing authentication helper; then use --resume"
                        )
                    break
                sleeper(float(2 ** attempt))
            except FyersImportError as exc:
                failures.append(f"{window.symbol} {window.first}: {exc}")
                break
        if result is None:
            if not failures:
                failures.append("request budget exhausted; use --resume after review")
            break
        observed_dates.update(item.day for item in result.reconciliation)
        missing_dates.update(day.isoformat() for day in result.missing_sessions)
        returned_candles += result.candles
        new_rows = _staged_rows(plan.staging) - rows_before
        already_staged_or_overlap += result.candles - new_rows
        states = {state: sum(item.state == state for item in result.reconciliation)
                  for state in ("MATCH", "CONFLICT", "NO_NSE_REFERENCE")}
        conflicts += states["CONFLICT"]
        results.append({
            "request_id": window.request_id, "symbol": window.symbol,
            "from_date": window.first.isoformat(),
            "to_date": window.last.isoformat(), "candles": result.candles,
            "fetched": result.fetched, "resumed_raw": result.resumed_raw,
            "checkpoint_skipped": result.checkpoint_skipped,
            "response_sha256": result.response_sha256,
            "raw_checkpoint": str(raw_path),
            "new_candles": new_rows,
            "already_staged_or_overlap": result.candles - new_rows,
            "missing_sessions": [d.isoformat() for d in result.missing_sessions],
            "reconciliation": states,
        })
        if progress is not None:
            completed = sum(r["symbol"] == window.symbol for r in results)
            progress(
                f"{window.symbol}: {completed} window(s) checked; "
                f"{requests} request(s) used"
            )
        if result.missing_sessions or states["CONFLICT"] or not result.candles:
            failures.append(
                f"{window.symbol} {window.first}: coverage or conflict review required"
            )
            break
    requested_by_symbol = {
        entry.symbol: sum(window.symbol == entry.symbol for window in plan.windows)
        for entry in plan.entries
    }
    completed_symbols = [
        symbol for symbol, expected in requested_by_symbol.items()
        if sum(item["symbol"] == symbol for item in results) == expected
        and not any(failure.startswith(f"{symbol} ") for failure in failures)
    ]
    return {
        "identity_state": "PROVISIONAL", "research_eligible": False,
        "canonical_database": str(plan.canonical),
        "staging_database": str(plan.staging),
        "snapshot": str(snapshot), "requested_windows": len(plan.windows),
        "completed_windows": len(results), "requests": requests,
        "requested_instruments": [entry.symbol for entry in plan.entries],
        "completed_instruments": completed_symbols,
        "windows": results, "failures": failures,
        "returned_candles": returned_candles,
        "already_staged_or_overlap": already_staged_or_overlap,
        "conflicting_observations": conflicts,
        "missing_sessions": sorted(missing_dates),
        "distinct_trading_sessions": len(observed_dates),
        "first_candle_date": min(observed_dates).isoformat()
        if observed_dates else None,
        "last_candle_date": max(observed_dates).isoformat()
        if observed_dates else None,
        "corporate_action_status": "UNVERIFIED",
        "point_in_time_availability": "UNVERIFIED",
        "calendar_sha256": plan.calendar_sha256,
        "master_sha256": plan.master_sha256,
        "universe_sha256": plan.universe_sha256,
    }
