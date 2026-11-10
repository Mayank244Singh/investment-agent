"""Offline FYERS import tests with invented candles and disposable SQLite files."""

import json
import socket
import sqlite3
from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.cli import main
from app.db.base import Base
from app.db.models.prices import DailyPrice, IngestionRun, Instrument, SourceObservation
from app.modules.research.intraday import ExchangeCalendar, ExchangeSession
from app.modules.research.readiness import CalendarEvidence
from app.services import fyers_import_service as imports
from app.services.fyers_historical_provider import (
    PILOT_SYMBOLS,
    FyersError,
    FyersHistoricalDataProvider,
)
from app.services.fyers_import_cli import _preflight, _temporary_path
from app.services.historical_transport import HttpResponse
from app.services.market_data_service import add_verified_mapping, record_observation

START = date(2026, 10, 29)
END = date(2026, 10, 30)
ISIN = "INE123A01012"
REVIEW = imports.IdentityReview(
    "TESTA", "NSE:TESTA-EQ", ISIN, START, END, "synthetic identity review",
)
MASTER: dict[str, object] = {
    "exSymbol": "TESTA", "symTicker": "NSE:TESTA-EQ", "exSeries": "EQ",
    "exchangeName": "NSE", "isin": ISIN,
}
NOW = datetime(2026, 10, 31, tzinfo=UTC)


def _row(day: date, close: str = "101.00", volume: int = 100) -> list[object]:
    stamp = datetime.combine(day, time(9, 15), ZoneInfo("Asia/Kolkata"))
    return [int(stamp.timestamp()), "100.00", "102.00", "99.00", close, volume]


def _payload(rows: list[list[object]]) -> bytes:
    return json.dumps({"s": "ok", "code": 200, "candles": rows}).encode()


class FakeTransport:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status
        self.calls = 0

    def get(self, url: str, headers: Mapping[str, str], timeout_seconds: float,
            max_response_bytes: int) -> HttpResponse:
        self.calls += 1
        return HttpResponse(self.status, self.body, {})


def _setup(tmp_path: Path) -> tuple[Engine, Path]:
    engine = create_engine(f"sqlite:///{tmp_path / 'pilot.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        session.add(Instrument(symbol="TESTA", series="EQ", isin=ISIN))
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    return engine, raw


def _calendar(*days: date) -> CalendarEvidence:
    return CalendarEvidence(ExchangeCalendar(tuple(
        ExchangeSession(day, time(9, 15), time(15, 30)) for day in days
    )), source_note="reviewed synthetic exchange calendar")


def _provider(
    rows: list[list[object]], *, status: int = 200, secret: str = "fake-secret",
) -> tuple[FyersHistoricalDataProvider, FakeTransport]:
    transport = FakeTransport(_payload(rows), status)
    provider = FyersHistoricalDataProvider(
        transport=transport, app_id_getter=lambda: "FAKE-100",
        token_getter=lambda: secret, clock=lambda: NOW,
    )
    return provider, transport


def _import(engine: Engine, raw: Path, provider: FyersHistoricalDataProvider,
            *, master: dict[str, object] | None = None,
            calendar: CalendarEvidence | None = None) -> imports.FyersImportResult:
    return imports.import_fyers_daily_window(
        engine, provider, review=REVIEW, master_record=master or MASTER,
        first=START, last=END, calendar=calendar or _calendar(START, END),
        raw_dir=raw,
    )


def _count(engine: Engine, model: Any) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_valid_import_persists_original_source_and_integer_paise(
    tmp_path: Path,
) -> None:
    engine, raw = _setup(tmp_path)
    provider, transport = _provider([_row(START), _row(END)])
    result = _import(engine, raw, provider)
    assert transport.calls == 1
    assert (result.candles, result.observations_created,
            result.canonical_created, result.conflicts) == (2, 2, 2, 0)
    assert result.missing_sessions == ()
    assert _count(engine, IngestionRun) == 1
    with Session(engine) as session:
        bars = list(session.scalars(select(DailyPrice).order_by(DailyPrice.trade_date)))
        observations = list(session.scalars(select(SourceObservation)))
        assert [bar.close_paise for bar in bars] == [10100, 10100]
        assert all(bar.source_observation_id for bar in bars)
        assert all(item.provider == "fyers" and item.origin == "source"
                   and item.retrieved_at_utc is not None for item in observations)
        assert all(item.event_at_utc is not None for item in observations)
        assert all(
            item.payload_sha256 and item.raw_payload_json for item in observations
        )
    assert len(list(raw.glob("*.json"))) == 1
    assert list(raw.glob("*.json"))[0].stat().st_mode & 0o077 == 0
    assert "fake-secret" not in list(raw.glob("*.json"))[0].read_text()
    engine.dispose()


def test_repeat_import_uses_checkpoint_without_second_request(tmp_path: Path) -> None:
    engine, raw = _setup(tmp_path)
    provider, transport = _provider([_row(START), _row(END)])
    _import(engine, raw, provider)
    again = _import(engine, raw, provider)
    assert again.checkpoint_skipped and again.resumed_raw
    assert transport.calls == 1
    assert (_count(engine, IngestionRun), _count(engine, SourceObservation),
            _count(engine, DailyPrice)) == (1, 2, 2)
    engine.dispose()


def test_checkpoint_rejects_changed_reviewed_identity(tmp_path: Path) -> None:
    engine, raw = _setup(tmp_path)
    provider, transport = _provider([_row(START)])
    _import(engine, raw, provider)
    envelope_path = next(raw.glob("*.json"))
    envelope = json.loads(envelope_path.read_text())
    envelope["isin"] = "INE999A01010"
    envelope_path.write_text(json.dumps(envelope))
    with pytest.raises(imports.FyersImportError, match="raw response envelope"):
        _import(engine, raw, provider)
    assert transport.calls == 1
    assert _count(engine, IngestionRun) == 1
    engine.dispose()


def test_mid_import_failure_rolls_back_then_resumes_raw_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, raw = _setup(tmp_path)
    provider, transport = _provider([_row(START), _row(END)])
    original = record_observation
    calls = 0

    def fail_second(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic mid-import failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(imports, "record_observation", fail_second)
    with pytest.raises(RuntimeError, match="mid-import"):
        _import(engine, raw, provider)
    assert _count(engine, IngestionRun) == 0
    assert _count(engine, SourceObservation) == 0
    assert _count(engine, DailyPrice) == 0
    assert len(list(raw.glob("*.json"))) == 1
    monkeypatch.setattr(imports, "record_observation", original)
    resumed = _import(engine, raw, provider)
    assert resumed.resumed_raw and not resumed.fetched
    assert transport.calls == 1
    assert _count(engine, DailyPrice) == 2
    engine.dispose()


def test_identity_mismatch_fails_before_request_or_write(tmp_path: Path) -> None:
    engine, raw = _setup(tmp_path)
    provider, transport = _provider([_row(START)])
    with pytest.raises(FyersError, match="identity"):
        _import(engine, raw, provider, master={**MASTER, "isin": "INE999A01010"})
    assert transport.calls == 0
    assert _count(engine, IngestionRun) == 0
    engine.dispose()


def test_existing_canonical_conflict_is_reported_and_preserved(tmp_path: Path) -> None:
    engine, raw = _setup(tmp_path)
    with Session(engine) as session, session.begin():
        session.add(DailyPrice(
            instrument_id=1, trade_date=START, open_paise=10000,
            high_paise=10200, low_paise=9900, close_paise=10000,
            volume=100, price_basis="unadjusted",
        ))
    provider, _ = _provider([_row(START)])
    result = _import(engine, raw, provider)
    assert result.conflicts == 1 and result.canonical_created == 0
    with Session(engine) as session:
        assert session.scalar(select(DailyPrice.close_paise)) == 10000
        assert session.scalar(select(SourceObservation.reconciliation)) == "conflict"
    engine.dispose()


def test_invalid_ohlcv_is_rejected_before_storage(tmp_path: Path) -> None:
    engine, raw = _setup(tmp_path)
    provider, _ = _provider([_row(START, close="103.00")])
    with pytest.raises(FyersError, match="OHLC"):
        _import(engine, raw, provider)
    assert _count(engine, IngestionRun) == 0
    assert list(raw.glob("*.json")) == []
    engine.dispose()


def test_missing_session_is_reported_not_filled(tmp_path: Path) -> None:
    engine, raw = _setup(tmp_path)
    provider, _ = _provider([_row(START)])
    result = _import(engine, raw, provider)
    assert result.missing_sessions == (END,)
    assert _count(engine, DailyPrice) == 1
    engine.dispose()


def test_unclassified_weekday_fails_before_network(tmp_path: Path) -> None:
    engine, raw = _setup(tmp_path)
    provider, transport = _provider([_row(START)])
    with pytest.raises(imports.FyersImportError, match="calendar does not classify"):
        _import(engine, raw, provider, calendar=_calendar(START))
    assert transport.calls == 0
    engine.dispose()


def test_provider_failure_does_not_leak_token_or_write(tmp_path: Path) -> None:
    engine, raw = _setup(tmp_path)
    secret = "never-disclose-this-synthetic-token"
    provider, transport = _provider([], status=403, secret=secret)
    with pytest.raises(FyersError) as error:
        _import(engine, raw, provider)
    assert secret not in str(error.value)
    assert transport.calls == 1
    assert _count(engine, IngestionRun) == 0
    assert list(raw.glob("*.json")) == []
    engine.dispose()


def test_import_cli_without_live_flags_cannot_open_socket(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network attempt")

    monkeypatch.setattr(socket, "socket", forbidden)
    result = main([
        "import-fyers-daily", "--database", "/private/tmp/nonexistent.db",
        "--raw-dir", "/private/tmp/nonexistent-raw", "--master-json", "unused",
        "--pilot-identities-json", "unused",
        "--calendar-json", "unused", "--symbol", "RELIANCE",
        "--ticker", "NSE:RELIANCE-EQ", "--isin", "INE002A01018",
        "--valid-from", "2026-10-01", "--valid-to", "2026-10-31",
        "--identity-evidence", "synthetic review", "--from-date", "2026-10-29",
        "--to-date", "2026-10-30", "--max-requests", "1",
    ])
    assert result == 2
    assert "No FYERS request made" in capsys.readouterr().out


def test_isolated_database_guard_rejects_hardlink(tmp_path: Path) -> None:
    original = tmp_path / "original.db"
    original.write_bytes(b"not a real database")
    original.chmod(0o600)
    alias = tmp_path / "alias.db"
    alias.hardlink_to(original)
    with pytest.raises(imports.FyersImportError, match="must be private"):
        _temporary_path(alias, directory=False)


def _pilot_inputs(tmp_path: Path, *, mapped: int = 5) -> tuple[Path, Path, Path, Path]:
    database = tmp_path / "five-stock-pilot.db"
    engine = create_engine(f"sqlite:///{database}")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        for symbol in PILOT_SYMBOLS:
            session.add(Instrument(symbol=symbol, series="EQ", isin=ISIN))
        session.flush()
        instruments = list(session.scalars(select(Instrument).where(
            Instrument.symbol.in_(PILOT_SYMBOLS),
        )))
        for instrument in instruments[:mapped]:
            add_verified_mapping(
                session, instrument_id=instrument.id, provider="fyers",
                provider_instrument_key=f"NSE:{instrument.symbol}-EQ",
                symbol=instrument.symbol, exchange="NSE", series="EQ",
                valid_from=START, valid_to=END,
            )
    engine.dispose()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute(
            "INSERT INTO alembic_version VALUES ('0004_market_data_foundation')"
        )
    database.chmod(0o600)
    identities = tmp_path / "identities.json"
    identities.write_text(json.dumps({
        symbol: {
            "ticker": f"NSE:{symbol}-EQ", "isin": ISIN,
            "valid_from": START.isoformat(), "valid_to": END.isoformat(),
            "evidence_note": "synthetic test review",
        } for symbol in PILOT_SYMBOLS
    }))
    master = tmp_path / "master.json"
    master.write_text(json.dumps({
        f"NSE:{symbol}-EQ": {
            "exSymbol": symbol, "symTicker": f"NSE:{symbol}-EQ",
            "exSeries": "EQ", "exchangeName": "NSE", "isin": ISIN,
        } for symbol in PILOT_SYMBOLS
    }))
    calendar = tmp_path / "calendar.json"
    calendar.write_text(json.dumps({
        "source_note": "synthetic reviewed calendar",
        "sessions": [
            {"date": day.isoformat(), "opens_local": "09:15:00",
             "closes_local": "15:30:00", "special": False}
            for day in (START, END)
        ],
        "holidays": [], "halts": [],
    }))
    return database, identities, master, calendar


def test_five_stock_offline_preflight_checks_schema_and_mappings(
    tmp_path: Path,
) -> None:
    database, identities, master, calendar = _pilot_inputs(tmp_path)
    reviews = _preflight(database, master, identities, calendar, START, END)
    assert set(reviews) == set(PILOT_SYMBOLS)
    assert all(review.evidence_note == "synthetic test review"
               for review in reviews.values())


def test_successful_preflight_cli_reads_no_credentials_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database, identities, master, calendar = _pilot_inputs(tmp_path)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(mode=0o700)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network attempt")

    monkeypatch.setattr(socket, "socket", forbidden)
    result = main([
        "import-fyers-daily", "--database", str(database),
        "--raw-dir", str(raw_dir), "--master-json", str(master),
        "--pilot-identities-json", str(identities),
        "--calendar-json", str(calendar), "--symbol", "RELIANCE",
        "--ticker", "NSE:RELIANCE-EQ", "--isin", ISIN,
        "--valid-from", START.isoformat(), "--valid-to", END.isoformat(),
        "--identity-evidence", "synthetic test review",
        "--from-date", START.isoformat(), "--to-date", END.isoformat(),
        "--max-requests", "1", "--preflight-only",
        "--app-config", str(tmp_path / "missing-config"),
        "--token-file", str(tmp_path / "missing-token"),
    ])
    assert result == 0
    assert "offline preflight passed" in capsys.readouterr().out
    assert not list(raw_dir.iterdir())


def test_missing_pilot_mapping_blocks_before_any_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database, identities, master, calendar = _pilot_inputs(tmp_path, mapped=4)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(mode=0o700)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network attempt")

    monkeypatch.setattr(socket, "socket", forbidden)
    result = main([
        "import-fyers-daily", "--database", str(database),
        "--raw-dir", str(raw_dir), "--master-json", str(master),
        "--pilot-identities-json", str(identities),
        "--calendar-json", str(calendar), "--symbol", "RELIANCE",
        "--ticker", "NSE:RELIANCE-EQ", "--isin", ISIN,
        "--valid-from", START.isoformat(), "--valid-to", END.isoformat(),
        "--identity-evidence", "synthetic test review",
        "--from-date", START.isoformat(), "--to-date", END.isoformat(),
        "--max-requests", "1", "--preflight-only",
    ])
    assert result == 1
    assert "mapping missing" in capsys.readouterr().err
    assert not list(raw_dir.iterdir())


def test_live_import_rejects_unapproved_temporary_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database, identities, master, calendar = _pilot_inputs(tmp_path)
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(mode=0o700)

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network attempt")

    monkeypatch.setattr(socket, "socket", forbidden)
    result = main([
        "import-fyers-daily", "--database", str(database),
        "--raw-dir", str(raw_dir), "--master-json", str(master),
        "--pilot-identities-json", str(identities),
        "--calendar-json", str(calendar), "--symbol", "RELIANCE",
        "--ticker", "NSE:RELIANCE-EQ", "--isin", ISIN,
        "--valid-from", START.isoformat(), "--valid-to", END.isoformat(),
        "--identity-evidence", "synthetic test review",
        "--from-date", START.isoformat(), "--to-date", END.isoformat(),
        "--max-requests", "1", "--allow-live", "--confirm-persist",
    ])
    assert result == 1
    assert "approved pilot paths" in capsys.readouterr().err
    assert not list(raw_dir.iterdir())


def test_missing_migration_or_table_blocks_offline_preflight(tmp_path: Path) -> None:
    database, identities, master, calendar = _pilot_inputs(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE source_observations")
    with pytest.raises(imports.FyersImportError, match="required market-data tables"):
        _preflight(database, master, identities, calendar, START, END)
