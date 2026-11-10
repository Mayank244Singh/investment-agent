"""Explicitly gated FYERS provisional staging command for the five-stock pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.modules.research.readiness_io import load_calendar_evidence
from app.services.fyers_historical_provider import (
    PILOT_SYMBOLS,
    TOKEN_FILE,
    FyersError,
    FyersHistoricalDataProvider,
    read_access_token,
)
from app.services.fyers_import_cli import (
    APPROVED_ROOT,
    _master_record,
    _private_approved_root,
    _require_migration,
    _temporary_path,
)
from app.services.fyers_import_service import FyersImportError, _expected_days
from app.services.fyers_provisional_service import (
    ProvisionalIdentity,
    initialize_staging,
    load_local_nse_references,
    stage_fyers_daily_window,
    verify_nse_references,
)

INDIA = ZoneInfo("Asia/Kolkata")
PILOT_FIRST = date(2026, 10, 12)
PILOT_LAST = date(2026, 11, 6)


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "stage-fyers-daily",
        help="Stage one provisional FYERS daily window outside canonical data",
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--staging-database", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--master-json", type=Path, required=True)
    parser.add_argument("--evidence-json", type=Path, required=True)
    parser.add_argument("--calendar-json", type=Path, required=True)
    parser.add_argument("--nse-zip-dir", type=Path, required=True)
    parser.add_argument("--symbol", choices=PILOT_SYMBOLS, required=True)
    parser.add_argument("--from-date", type=date.fromisoformat, required=True)
    parser.add_argument("--to-date", type=date.fromisoformat, required=True)
    parser.add_argument("--max-requests", type=int, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--confirm-persist", action="store_true")
    parser.add_argument("--app-config", type=Path,
                        default=Path.home() / "fyers-auth" / ".env")
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)


def _staging_path(path: Path, canonical: Path) -> Path:
    """Restrict the second DB to private temporary or approved pilot storage."""
    candidate = path.expanduser()
    if candidate.is_symlink():
        raise FyersImportError("provisional staging path must not be a symlink")
    resolved = candidate.resolve(strict=False)
    roots = (Path(tempfile.gettempdir()).resolve(), APPROVED_ROOT.resolve())
    if not any(resolved.is_relative_to(root) for root in roots):
        raise FyersImportError("provisional staging path is outside isolation")
    if resolved == canonical:
        raise FyersImportError("provisional and canonical databases must differ")
    parent = resolved.parent.stat()
    if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
            or parent.st_mode & 0o077):
        raise FyersImportError("provisional staging directory must be private")
    return resolved


def _evidence(path: Path, master_path: Path) -> dict[str, frozenset[date]]:
    """Evidence is informational: it never grants verified identity status."""
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077 or metadata.st_size > 100_000):
        raise FyersImportError("historical evidence file must be private")
    if master_path.stat().st_size > 50_000_000:
        raise FyersImportError("FYERS master file exceeds 50 MB")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(payload, dict)
            or not isinstance(payload.get("instruments"), dict)
            or set(payload["instruments"]) != set(PILOT_SYMBOLS)
            or not isinstance(payload.get("fyers_current_master"), dict)
            or payload["fyers_current_master"].get("sha256")
            != hashlib.sha256(master_path.read_bytes()).hexdigest()
            or not isinstance(payload.get("nse_sources"), list)):
        raise FyersImportError("historical evidence/master review does not match")
    source_dates = set()
    for source in payload["nse_sources"]:
        if (not isinstance(source, dict)
                or not isinstance(source.get("sha256"), str)
                or len(source["sha256"]) != 64
                or not isinstance(source.get("filename"), str)
                or not isinstance(source.get("trade_date"), str)):
            raise FyersImportError("NSE evidence source is invalid")
        source_dates.add(date.fromisoformat(source["trade_date"]))
    dates: dict[str, frozenset[date]] = {}
    for symbol in PILOT_SYMBOLS:
        item = payload["instruments"][symbol]
        if (not isinstance(item, dict)
                or item.get("fyers_historical_identity_status") is None
                or item.get("verified_mapping_created") is not False
                or not isinstance(item.get("nse_verified_dates"), list)):
            raise FyersImportError("provisional evidence must not claim verification")
        parsed = frozenset(
            date.fromisoformat(raw) for raw in item["nse_verified_dates"]
        )
        if len(parsed) != len(item["nse_verified_dates"]):
            raise FyersImportError("duplicate NSE evidence date")
        if not parsed <= source_dates:
            raise FyersImportError("NSE identity date lacks source evidence")
        dates[symbol] = parsed
    return dates


def _identities(
    database: Path, master_path: Path, evidence_path: Path,
) -> dict[str, ProvisionalIdentity]:
    evidence = _evidence(evidence_path, master_path)
    review = json.loads(evidence_path.read_text(encoding="utf-8"))
    master_sha = hashlib.sha256(master_path.read_bytes()).hexdigest()
    identities: dict[str, ProvisionalIdentity] = {}
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as canonical:
        canonical.execute("PRAGMA query_only=ON")
        for symbol in PILOT_SYMBOLS:
            rows = canonical.execute(
                "SELECT id, isin FROM instruments WHERE symbol=? AND series='EQ'",
                (symbol,),
            ).fetchall()
            if len(rows) != 1 or not isinstance(rows[0][1], str):
                raise FyersImportError(f"{symbol}: internal NSE identity missing")
            ticker = f"NSE:{symbol}-EQ"
            record = _master_record(master_path, ticker)
            if (record.get("exSymbol") != symbol
                    or record.get("symTicker") != ticker
                    or record.get("exSeries") != "EQ"
                    or record.get("exchangeName") != "NSE"
                    or record.get("isin") != rows[0][1]):
                raise FyersImportError(f"{symbol}: current FYERS master mismatch")
            evidence_item = review["instruments"][symbol]
            if (evidence_item.get("isin") != rows[0][1]
                    or evidence_item.get("fyers_current_ticker") != ticker):
                raise FyersImportError(f"{symbol}: NSE review identity mismatch")
            identities[symbol] = ProvisionalIdentity(
                symbol, ticker, rows[0][1], rows[0][0], evidence[symbol],
                master_sha,
            )
    return identities


def _app_id(path: Path) -> str:
    from app.services.fyers_import_cli import _app_id as existing_app_id

    return existing_app_id(path)


def run(arguments: argparse.Namespace) -> int:
    """Only explicit live flags can reach the one-request provider."""
    if not arguments.preflight_only and not (
        arguments.allow_live and arguments.confirm_persist
    ):
        print("No FYERS request made; --allow-live and --confirm-persist required.")
        return 2
    try:
        if arguments.max_requests != 1:
            raise FyersImportError("one-request cap must be exactly 1")
        first, last = arguments.from_date, arguments.to_date
        if (first != PILOT_FIRST or last != PILOT_LAST
                or last >= datetime.now(INDIA).date()):
            raise FyersImportError(
                "only the completed approved pilot window is allowed"
            )
        canonical = _temporary_path(arguments.database, directory=False)
        raw_dir = _temporary_path(arguments.raw_dir, directory=True)
        staging = _staging_path(arguments.staging_database, canonical)
        if canonical == (APPROVED_ROOT / "pilot.db").resolve():
            _private_approved_root()
        if not arguments.preflight_only and (
            canonical != (APPROVED_ROOT / "pilot.db").resolve()
            or staging != (APPROVED_ROOT / "provisional.db").resolve()
            or raw_dir != (APPROVED_ROOT / "raw").resolve()
        ):
            raise FyersImportError("live staging requires approved pilot paths")
        _require_migration(canonical)
        calendar = load_calendar_evidence(arguments.calendar_json)
        expected = _expected_days(calendar, first, last)
        identities = _identities(
            canonical, arguments.master_json, arguments.evidence_json,
        )
        reviewed = json.loads(arguments.evidence_json.read_text(encoding="utf-8"))
        nse_references = load_local_nse_references(
            arguments.nse_zip_dir, reviewed["nse_sources"], identities,
        )
        verify_nse_references(canonical, identities, nse_references)
        initialize_staging(staging)
        identity = identities[arguments.symbol]
        missing_evidence = sorted(set(expected) - identity.nse_evidence_dates)
        if arguments.preflight_only:
            print(
                f"Provisional preflight passed for five current identities; "
                f"{len(expected)} sessions; {len(nse_references)} local NSE "
                f"reference rows; {len(missing_evidence)} dates "
                "without independent NSE identity evidence for selected symbol. "
                "No credential read or FYERS request."
            )
            print(
                "Dates without NSE identity evidence: "
                + ", ".join(day.isoformat() for day in missing_evidence)
            )
            return 0
        provider = FyersHistoricalDataProvider(
            app_id_getter=lambda: _app_id(arguments.app_config),
            token_getter=lambda: read_access_token(arguments.token_file),
        )
        result = stage_fyers_daily_window(
            canonical, staging, provider, identity=identity, first=first,
            last=last, calendar=calendar, raw_dir=raw_dir,
            nse_references=nse_references,
        )
        print(json.dumps({
            "identity_state": "PROVISIONAL",
            "symbol": result.symbol,
            "candles": result.candles,
            "fetched": result.fetched,
            "resumed_raw": result.resumed_raw,
            "checkpoint_skipped": result.checkpoint_skipped,
            "missing_sessions": [day.isoformat() for day in result.missing_sessions],
            "missing_nse_identity_dates": [
                day.isoformat() for day in result.missing_nse_identity_dates
            ],
            "reconciliation": [
                {"date": row.day.isoformat(), "state": row.state,
                 "nse_identity_evidence": row.nse_identity_evidence,
                 "differences": dict(row.differences)}
                for row in result.reconciliation
            ],
            "response_sha256": result.response_sha256,
        }, sort_keys=True))
        return 0
    except (FyersError, FyersImportError, OSError, ValueError,
            sqlite3.Error, TypeError, KeyError) as exc:
        print(f"FYERS provisional staging stopped: {exc}", file=sys.stderr)
        return 1
