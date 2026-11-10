"""Explicitly gated, one-window FYERS import command for an isolated database."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import dotenv_values
from sqlalchemy import create_engine, event
from sqlalchemy.exc import SQLAlchemyError

from app.db.models.prices import Instrument
from app.modules.research.readiness_io import load_calendar_evidence
from app.services.fyers_historical_provider import (
    PILOT_SYMBOLS,
    TOKEN_FILE,
    FyersError,
    FyersHistoricalDataProvider,
    read_access_token,
    verify_master_mapping,
)
from app.services.fyers_import_service import (
    FyersImportError,
    IdentityReview,
    _expected_days,
    import_fyers_daily_window,
)
from app.services.market_data_service import MarketDataError

INDIA = ZoneInfo("Asia/Kolkata")
MASTER_LIMIT = 50_000_000
APPROVED_ROOT = Path.home() / "InvestmentAgentData" / "pilots" / "fyers-b1"


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "import-fyers-daily",
        help="One gated FYERS daily window into the approved isolated database",
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--master-json", type=Path, required=True)
    parser.add_argument("--pilot-identities-json", type=Path, required=True)
    parser.add_argument("--calendar-json", type=Path, required=True)
    parser.add_argument("--symbol", choices=PILOT_SYMBOLS, required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--isin", required=True)
    parser.add_argument("--valid-from", type=date.fromisoformat, required=True)
    parser.add_argument("--valid-to", type=date.fromisoformat, required=True)
    parser.add_argument("--identity-evidence", required=True)
    parser.add_argument("--from-date", type=date.fromisoformat, required=True)
    parser.add_argument("--to-date", type=date.fromisoformat, required=True)
    parser.add_argument("--app-config", type=Path,
                        default=Path.home() / "fyers-auth" / ".env")
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    parser.add_argument("--max-requests", type=int, required=True)
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--confirm-persist", action="store_true")
    parser.add_argument(
        "--preflight-only", action="store_true",
        help="Check all five identities, schema and paths offline; make no request",
    )


def _temporary_path(path: Path, *, directory: bool) -> Path:
    try:
        resolved = path.expanduser().resolve(strict=True)
        metadata = path.expanduser().lstat()
    except OSError:
        raise FyersImportError("isolated pilot path does not exist") from None
    roots = (
        Path(tempfile.gettempdir()).resolve(), Path("/private/tmp"),
        APPROVED_ROOT.resolve(),
    )
    if not any(resolved.is_relative_to(root) for root in roots):
        raise FyersImportError("FYERS pilot path is outside approved isolation")
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or (not directory and metadata.st_nlink != 1)):
        raise FyersImportError("FYERS pilot database/raw directory must be private")
    return resolved


def _private_approved_root() -> None:
    for path in (APPROVED_ROOT.parent.parent, APPROVED_ROOT.parent, APPROVED_ROOT):
        try:
            metadata = path.lstat()
        except OSError:
            raise FyersImportError("approved pilot storage is unavailable") from None
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077):
            raise FyersImportError("approved pilot storage must be private")


def _require_migration(database: Path) -> None:
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise FyersImportError("isolated database failed SQLite integrity check")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise FyersImportError("isolated database has foreign-key violations")
        required = {
            "instruments", "instrument_mappings", "ingestion_runs",
            "source_observations", "daily_prices", "alembic_version",
        }
        existing = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not required <= existing:
            raise FyersImportError(
                "isolated database lacks required market-data tables: "
                + ", ".join(sorted(required - existing))
            )
        versions = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchall()
    if versions != [("0004_market_data_foundation",)]:
        raise FyersImportError("isolated database requires the current migration")


def _master_record(path: Path, ticker: str) -> dict[str, object]:
    if path.stat().st_size > MASTER_LIMIT:
        raise FyersImportError("FYERS master file exceeds 50 MB")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get(ticker), dict):
        raise FyersImportError("FYERS master lacks the reviewed ticker")
    return dict(payload[ticker])


def _pilot_reviews(path: Path) -> dict[str, IdentityReview]:
    try:
        if path.stat().st_size > 50_000:
            raise FyersImportError("pilot identity manifest exceeds 50 KB")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        raise FyersImportError("pilot identity manifest is unavailable") from None
    if not isinstance(payload, dict) or set(payload) != set(PILOT_SYMBOLS):
        raise FyersImportError("pilot identity manifest needs all five symbols")
    reviews: dict[str, IdentityReview] = {}
    for symbol in PILOT_SYMBOLS:
        item = payload[symbol]
        if not isinstance(item, dict) or set(item) != {
            "ticker", "isin", "valid_from", "valid_to", "evidence_note",
        } or not all(isinstance(value, str) for value in item.values()):
            raise FyersImportError("pilot identity manifest has invalid fields")
        reviews[symbol] = IdentityReview(
            symbol, item["ticker"], item["isin"],
            date.fromisoformat(item["valid_from"]),
            date.fromisoformat(item["valid_to"]), item["evidence_note"],
        )
    return reviews


def _preflight(
    database: Path, master_path: Path,
    identities_path: Path, calendar_path: Path,
    first: date, last: date,
) -> dict[str, IdentityReview]:
    """Check the entire five-stock pilot without credentials, network or writes."""
    _require_migration(database)
    reviews = _pilot_reviews(identities_path)
    calendar = load_calendar_evidence(calendar_path)
    if first > last or (last - first).days >= 31:
        raise FyersImportError("pilot window must be within 31 calendar days")
    _expected_days(calendar, first, last)
    with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        for symbol, review in reviews.items():
            if (
                review.ticker != f"NSE:{symbol}-EQ"
                or first < review.valid_from or last > review.valid_to
            ):
                raise FyersImportError(f"{symbol}: historical identity is unverified")
            rows = connection.execute(
                "SELECT id, isin FROM instruments WHERE symbol=? AND series='EQ'",
                (symbol,),
            ).fetchall()
            if (len(rows) != 1 or type(rows[0][0]) is not int
                    or rows[0][0] <= 0 or rows[0][1] != review.isin):
                raise FyersImportError(f"{symbol}: internal EQ instrument/ISIN missing")
            master = _master_record(master_path, review.ticker)
            verify_master_mapping(
                Instrument(id=rows[0][0], symbol=symbol, series="EQ",
                           isin=review.isin), master,
                valid_from=review.valid_from, valid_to=review.valid_to,
            )
            mapping_rows = connection.execute(
                "SELECT instrument_id, provider_instrument_key, symbol "
                "FROM instrument_mappings WHERE provider='fyers' "
                "AND (provider_instrument_key=? OR symbol=?) "
                "AND exchange='NSE' AND series='EQ' "
                "AND identity_status='verified' AND valid_from<=? "
                "AND valid_to>=?",
                (review.ticker, symbol, first.isoformat(), last.isoformat()),
            ).fetchall()
            if mapping_rows != [(rows[0][0], review.ticker, symbol)]:
                raise FyersImportError(f"{symbol}: reviewed FYERS mapping missing")
    return reviews


def _app_id(path: Path) -> str:
    metadata = path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
    ):
        raise FyersImportError("FYERS App ID file must be private and regular")
    value = dotenv_values(path).get("FYERS_APP_ID")
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", value):
        raise FyersImportError("FYERS App ID is unavailable or invalid")
    return value


def run(arguments: argparse.Namespace) -> int:
    """Refuse all network access until both flags and a one-request cap are set."""
    if not arguments.preflight_only and not (
        arguments.allow_live and arguments.confirm_persist
    ):
        print("No FYERS request made; --allow-live and --confirm-persist required.")
        return 2
    try:
        if arguments.max_requests != 1:
            raise FyersImportError("one-request cap must be exactly 1")
        if arguments.to_date >= datetime.now(INDIA).date():
            raise FyersImportError("daily import requires completed past dates")
        database = _temporary_path(arguments.database, directory=False)
        raw_dir = _temporary_path(arguments.raw_dir, directory=True)
        if database == (APPROVED_ROOT / "pilot.db").resolve():
            _private_approved_root()
        if not arguments.preflight_only and (
            database != (APPROVED_ROOT / "pilot.db").resolve()
            or raw_dir != (APPROVED_ROOT / "raw").resolve()
        ):
            raise FyersImportError("live FYERS import requires approved pilot paths")
        review = IdentityReview(
            arguments.symbol, arguments.ticker, arguments.isin,
            arguments.valid_from, arguments.valid_to,
            arguments.identity_evidence,
        )
        reviews = _preflight(
            database, arguments.master_json, arguments.pilot_identities_json,
            arguments.calendar_json, arguments.from_date, arguments.to_date,
        )
        if review != reviews[arguments.symbol]:
            raise FyersImportError("requested identity differs from pilot review")
        if arguments.preflight_only:
            print("FYERS pilot offline preflight passed for five instruments; "
                  "no credential read, request or database write.")
            return 0
        master = _master_record(arguments.master_json, review.ticker)
        calendar = load_calendar_evidence(arguments.calendar_json)
        app_id = _app_id(arguments.app_config)
        # The token is read only if the window has no committed/raw checkpoint.
        provider = FyersHistoricalDataProvider(
            app_id_getter=lambda: app_id,
            token_getter=lambda: read_access_token(arguments.token_file),
        )
        engine = create_engine(f"sqlite:///{database}")

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection: object, _: object) -> None:
            cursor = connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        try:
            result = import_fyers_daily_window(
                engine, provider, review=review, master_record=master,
                first=arguments.from_date, last=arguments.to_date,
                calendar=calendar, raw_dir=raw_dir,
            )
        finally:
            engine.dispose()
        print(json.dumps(asdict(result), default=str, sort_keys=True))
        return 0
    except (FyersError, FyersImportError, MarketDataError, ValueError,
            OSError, sqlite3.Error, SQLAlchemyError) as exc:
        # Never print provider payloads, SQL parameters, App ID or token material.
        detail = str(exc) if isinstance(
            exc, (FyersError, FyersImportError, MarketDataError)
        ) else type(exc).__name__
        print(f"FYERS import stopped: {detail}", file=sys.stderr)
        return 1
