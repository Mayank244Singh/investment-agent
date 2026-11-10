"""Offline-preflight-first CLI for provisional daily FYERS history collection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sqlite3
import stat
import sys
from datetime import date
from pathlib import Path

from app.services.fyers_historical_provider import (
    TOKEN_FILE,
    FyersError,
    FyersHistoricalDataProvider,
    read_access_token,
)
from app.services.fyers_history_collector import collect, prepare_plan
from app.services.fyers_import_cli import APPROVED_ROOT, _temporary_path
from app.services.fyers_import_service import FyersImportError
from app.services.fyers_provisional_cli import _staging_path
from app.services.nse_reference_validator import ReferenceValidationError


def add_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser(
        "collect-fyers-history",
        help="Calendar-gated, provisional FYERS daily history collection",
    )
    parser.add_argument("--universe-file", type=Path, required=True)
    parser.add_argument("--from-date", type=date.fromisoformat, required=True)
    parser.add_argument("--to-date", type=date.fromisoformat, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--staging-database", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--master-json", type=Path, required=True)
    parser.add_argument("--evidence-json", type=Path, required=True)
    parser.add_argument("--calendar-json", type=Path, required=True)
    parser.add_argument("--nse-zip-dir", type=Path, required=True)
    parser.add_argument("--supplemental-manifest", type=Path)
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    parser.add_argument("--max-requests", type=int, required=True)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--confirm-persist", action="store_true")
    parser.add_argument("--resume", action="store_true")


def _private_root() -> None:
    for path in (APPROVED_ROOT.parent.parent, APPROVED_ROOT.parent, APPROVED_ROOT):
        metadata = path.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077):
            raise FyersImportError("approved pilot storage must be private")


def _save_report(raw_dir: Path, report: dict[str, object], key: str) -> Path:
    path = raw_dir / f"collection-report-{key}.json"
    temporary = raw_dir / f".collection-report-{key}.{secrets.token_hex(8)}.tmp"
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(report, stream, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def run(arguments: argparse.Namespace) -> int:
    """Never read credentials or make a GET during either offline mode."""
    offline = arguments.preflight_only or arguments.dry_run
    if not offline and not (arguments.allow_live and arguments.confirm_persist):
        print("No FYERS request made; --allow-live and --confirm-persist required.")
        return 2
    try:
        if arguments.max_requests <= 0:
            raise FyersImportError("max requests must be positive")
        canonical = _temporary_path(arguments.database, directory=False)
        staging = _staging_path(arguments.staging_database, canonical)
        raw_dir = _temporary_path(arguments.raw_dir, directory=True)
        if not staging.is_file():
            raise FyersImportError("private provisional database must exist")
        if not offline:
            _private_root()
            if (canonical != (APPROVED_ROOT / "pilot.db").resolve()
                    or staging != (APPROVED_ROOT / "provisional.db").resolve()
                    or raw_dir != (APPROVED_ROOT / "raw").resolve()):
                raise FyersImportError("live collection requires approved pilot paths")
        plan = prepare_plan(
            canonical=canonical, staging=staging, raw_dir=raw_dir,
            universe_file=arguments.universe_file,
            master_file=arguments.master_json,
            evidence_file=arguments.evidence_json,
            calendar_file=arguments.calendar_json,
            nse_zip_dir=arguments.nse_zip_dir,
            supplemental_manifest=arguments.supplemental_manifest,
            first=arguments.from_date, last=arguments.to_date,
        )
        print(json.dumps({
            "preflight": "PASS", "identity_state": "PROVISIONAL",
            "research_eligible": False,
            "instruments": [item.symbol for item in plan.entries],
            "windows": len(plan.windows), "request_budget": arguments.max_requests,
            "nse_reference_rows": len(plan.references),
            "credentials_read": False, "database_written": False,
        }, sort_keys=True))
        if offline:
            return 0
        before = hashlib.sha256(canonical.read_bytes()).hexdigest()
        provider = FyersHistoricalDataProvider(
            app_id_getter=lambda: os.environ.get("FYERS_APP_ID"),
            token_getter=lambda: read_access_token(arguments.token_file),
        )
        report = collect(
            plan, provider, max_requests=arguments.max_requests,
            resume=arguments.resume,
            progress=print,
        )
        report["canonical_hash_unchanged"] = (
            hashlib.sha256(canonical.read_bytes()).hexdigest() == before
        )
        key = hashlib.sha256(
            f"{plan.universe_sha256}:{arguments.from_date}:{arguments.to_date}".encode()
        ).hexdigest()[:16]
        path = _save_report(raw_dir, report, key)
        print(json.dumps({
            "requests": report["requests"],
            "completed_windows": report["completed_windows"],
            "requested_windows": report["requested_windows"],
            "failures": report["failures"],
            "report": str(path),
            "identity_state": "PROVISIONAL", "research_eligible": False,
            "canonical_hash_unchanged": report["canonical_hash_unchanged"],
        }, sort_keys=True))
        return 0 if (
            not report["failures"]
            and report["completed_windows"] == report["requested_windows"]
            and report["canonical_hash_unchanged"]
        ) else 1
    except (FyersImportError, ReferenceValidationError, FyersError) as exc:
        print(f"FYERS collection BLOCKED: {exc}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        print("FYERS collection BLOCKED: offline evidence, calendar, storage "
              "or provider check failed. No further request will be made.",
              file=sys.stderr)
        return 2
