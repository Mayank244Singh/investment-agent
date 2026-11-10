"""Offline NSE reference validation against invented provisional candles."""

import csv
import hashlib
import io
import json
import socket
import sqlite3
import zipfile
from datetime import date
from functools import partial
from pathlib import Path
from typing import TypedDict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import cli as cli_module
from app.cli import main
from app.db.base import Base
from app.db.models.prices import Instrument
from app.services.fyers_provisional_service import initialize_staging
from app.services.nse_reference_validator import (
    ReferenceValidationError,
    validate_references,
)
from app.services.price_import_service import HEADERS

FIRST = date(2026, 10, 29)
LAST = date(2026, 10, 30)
ISINS = {"TESTA": "INE123A01012", "TESTB": "INE456B01012"}


class Inputs(TypedDict):
    canonical_path: Path
    staging_path: Path
    calendar_path: Path
    evidence_path: Path
    reference_dirs: tuple[Path, ...]
    first: date
    last: date
    symbols: tuple[str, ...]


def _name(day: date) -> str:
    return f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"


def _zip(directory: Path, day: date, *, close: str = "101.00",
         wrong_header: bool = False, wrong_date: bool = False,
         wrong_member: bool = False, wrong_isin: bool = False,
         duplicate: bool = False) -> tuple[Path, str]:
    name = _name(day)
    output = io.StringIO()
    columns = list(HEADERS)
    if wrong_header:
        columns[-1] = "Unexpected"
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    for symbol in ISINS:
        row = {field: "" for field in HEADERS}
        row.update({
            "TradDt": FIRST.isoformat() if wrong_date else day.isoformat(),
            "BizDt": day.isoformat(), "Sgmt": "CM", "Src": "NSE",
            "FinInstrmTp": "STK", "ISIN": ("INE000A01012" if wrong_isin
                                              and symbol == "TESTA" else ISINS[symbol]),
            "TckrSymb": symbol, "SctySrs": "EQ", "OpnPric": "100.00",
            "HghPric": "102.00", "LwPric": "99.00", "ClsPric": close,
            "PrvsClsgPric": "100.00", "TtlTradgVol": "100",
        })
        if wrong_header:
            row.pop(HEADERS[-1])
            row["Unexpected"] = ""
        writer.writerow(row)
        if duplicate and symbol == "TESTA":
            writer.writerow(row)
    path = directory / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("other.csv" if wrong_member else name.removesuffix(".zip"),
                         output.getvalue())
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _setup(tmp_path: Path) -> Inputs:
    canonical = tmp_path / "canonical.db"
    engine = create_engine(f"sqlite:///{canonical}")
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
    with Session(engine) as session, session.begin():
        for symbol, isin in ISINS.items():
            session.add(Instrument(symbol=symbol, series="EQ", isin=isin))
    ids: dict[str, int] = {}
    with sqlite3.connect(canonical) as db:
        for symbol in ISINS:
            ids[symbol] = db.execute(
                "SELECT id FROM instruments WHERE symbol=?", (symbol,)
            ).fetchone()[0]
    engine.dispose()
    staging = tmp_path / "provisional.db"
    initialize_staging(staging)
    with sqlite3.connect(staging) as db:
        for symbol, isin in ISINS.items():
            cursor = db.execute(
                "INSERT INTO provisional_runs (source_ref,response_sha256,"
                "retrieved_at_utc,symbol,ticker,isin,internal_id,master_sha256,"
                "first_date,last_date,identity_state) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"offline:{symbol}", "a" * 64, "2026-11-09T00:00:00+00:00",
                 symbol, f"NSE:{symbol}-EQ", isin, ids[symbol], "b" * 64,
                 FIRST.isoformat(), LAST.isoformat(), "PROVISIONAL"),
            )
            for day in (FIRST, LAST):
                db.execute(
                    "INSERT INTO provisional_candles (run_id,source_record_key,"
                    "trade_date,event_at_utc,payload_sha256,open_paise,high_paise,"
                    "low_paise,close_paise,volume,reconciliation_state,"
                    "nse_identity_evidence,differences_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cursor.lastrowid, f"{symbol}:{day}", day.isoformat(),
                     f"{day}T00:00:00+00:00", "c" * 64, 10000, 10200,
                     9900, 10100, 100, "NO_NSE_REFERENCE", 0, "{}"),
                )
    calendar = tmp_path / "calendar.json"
    calendar.write_text(json.dumps({
        "source_note": "synthetic calendar",
        "sessions": [{"date": day.isoformat(), "opens_local": "09:15:00",
                      "closes_local": "15:30:00", "special": False}
                     for day in (FIRST, LAST)],
        "holidays": [], "halts": [],
    }))
    references = tmp_path / "references"
    references.mkdir()
    _, digest = _zip(references, FIRST)
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({
        "nse_sources": [{"trade_date": FIRST.isoformat(),
                         "filename": _name(FIRST), "sha256": digest}],
        "instruments": {symbol: {"isin": isin} for symbol, isin in ISINS.items()},
    }))
    return {"canonical_path": canonical, "staging_path": staging,
            "calendar_path": calendar, "evidence_path": evidence,
            "reference_dirs": (references,), "first": FIRST, "last": LAST,
            "symbols": tuple(ISINS)}


def _supplement(tmp_path: Path, digest: str) -> Path:
    path = tmp_path / "supplement.json"
    path.write_text(json.dumps({"sources": [{
        "trade_date": LAST.isoformat(), "filename": _name(LAST),
        "sha256": digest, "source_url": "https://www.nseindia.com/all-reports",
        "acquired_at_utc": "2026-11-09T12:00:00+00:00",
    }]}))
    return path


def test_missing_reference_is_reported_and_databases_unchanged(tmp_path: Path) -> None:
    kwargs = _setup(tmp_path)
    before = [hashlib.sha256(kwargs[key].read_bytes()).hexdigest()
              for key in ("canonical_path", "staging_path")]
    report = validate_references(**kwargs)
    assert not report.ready_for_reference_review
    assert report.summary()["comparison_counts"] == {
        "MATCH": 2, "NO_NSE_REFERENCE": 2,
    }
    assert report.summary()["missing_dates"] == [LAST.isoformat()]
    assert report.database_hashes_unchanged
    assert before == [hashlib.sha256(kwargs[key].read_bytes()).hexdigest()
                      for key in ("canonical_path", "staging_path")]


def test_all_references_match_with_private_supplement(tmp_path: Path) -> None:
    kwargs = _setup(tmp_path)
    _, digest = _zip(kwargs["reference_dirs"][0], LAST)
    report = validate_references(
        **kwargs, supplemental_manifest=_supplement(tmp_path, digest)
    )
    assert report.ready_for_reference_review
    assert report.summary()["comparison_counts"] == {"MATCH": 4}
    assert report.summary()["research_eligible"] is False


def test_signed_difference_is_reported_without_overwrite(tmp_path: Path) -> None:
    kwargs = _setup(tmp_path)
    _, digest = _zip(kwargs["reference_dirs"][0], LAST, close="101.01")
    report = validate_references(
        **kwargs, supplemental_manifest=_supplement(tmp_path, digest)
    )
    assert not report.ready_for_reference_review
    assert [(item.symbol, item.differences) for item in report.comparisons
            if item.status == "CONFLICT"] == [
        ("TESTA", {"close_paise": -1}), ("TESTB", {"close_paise": -1})
    ]
    assert report.database_hashes_unchanged


@pytest.mark.parametrize("problem", ["hash", "header", "date", "member",
                                       "isin", "duplicate"])
def test_invalid_supplemental_zip_fails_closed(tmp_path: Path,
                                               problem: str) -> None:
    kwargs = _setup(tmp_path)
    _, digest = _zip(
        kwargs["reference_dirs"][0], LAST,
        wrong_header=problem == "header", wrong_date=problem == "date",
        wrong_member=problem == "member", wrong_isin=problem == "isin",
        duplicate=problem == "duplicate",
    )
    if problem == "hash":
        digest = "0" * 64
    report = validate_references(
        **kwargs, supplemental_manifest=_supplement(tmp_path, digest)
    )
    assert not report.ready_for_reference_review
    assert report.database_hashes_unchanged
    assert any(item.status != "MATCH" for item in report.comparisons
               if item.day == LAST.isoformat())


def test_unregistered_file_and_duplicate_manifest_fail_closed(tmp_path: Path) -> None:
    kwargs = _setup(tmp_path)
    _, digest = _zip(kwargs["reference_dirs"][0], LAST)
    report = validate_references(**kwargs)
    assert "no hash/provenance record" in " ".join(report.issues)
    assert not report.ready_for_reference_review
    supplement = _supplement(tmp_path, digest)
    payload = json.loads(supplement.read_text())
    payload["sources"].append(payload["sources"][0])
    supplement.write_text(json.dumps(payload))
    with pytest.raises(ReferenceValidationError, match="duplicate"):
        validate_references(**kwargs, supplemental_manifest=supplement)


def test_validator_and_cli_never_open_a_network_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    kwargs = _setup(tmp_path)

    def forbidden(*args: object, **other: object) -> None:
        raise AssertionError("unexpected network socket")

    monkeypatch.setattr(socket, "socket", forbidden)
    assert not validate_references(**kwargs).ready_for_reference_review
    monkeypatch.setattr(
        cli_module, "validate_references",
        partial(validate_references, first=FIRST, last=LAST,
                symbols=tuple(ISINS)),
    )
    result = main([
        "validate-nse-references",
        "--canonical-database", str(kwargs["canonical_path"]),
        "--staging-database", str(kwargs["staging_path"]),
        "--calendar-json", str(kwargs["calendar_path"]),
        "--evidence-json", str(kwargs["evidence_path"]),
        "--reference-dir", str(kwargs["reference_dirs"][0]),
    ])
    assert result == 1  # Missing second reference is reported, with no GET.
    assert json.loads(capsys.readouterr().out)["comparison_counts"] == {
        "MATCH": 2, "NO_NSE_REFERENCE": 2,
    }
