"""Synthetic, offline tests for isolated provisional FYERS daily staging."""

import csv
import hashlib
import io
import json
import socket
import sqlite3
import zipfile
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.cli import main
from app.db.base import Base
from app.db.models.prices import DailyPrice, ImportBatch, Instrument, SourceObservation
from app.modules.research.intraday import ExchangeCalendar, ExchangeSession
from app.modules.research.readiness import CalendarEvidence
from app.services.fyers_historical_provider import FyersHistoricalDataProvider
from app.services.fyers_import_service import FyersImportError
from app.services.fyers_provisional_cli import _evidence
from app.services.fyers_provisional_service import (
    IdentityState,
    ProvisionalIdentity,
    ProvisionalResult,
    _raw_path,
    _write_raw,
    initialize_staging,
    load_local_nse_references,
    stage_fyers_daily_window,
    verify_nse_references,
)
from app.services.historical_transport import HttpResponse
from app.services.price_import_service import HEADERS

FIRST = date(2026, 10, 29)
LAST = date(2026, 10, 30)
ISIN = "INE123A01012"


class FakeTransport:
    def __init__(self, rows: list[list[object]], status: int = 200) -> None:
        self.body = json.dumps({"s": "ok", "code": 200, "candles": rows}).encode()
        self.status = status
        self.calls = 0

    def get(self, url: str, headers: Mapping[str, str], timeout_seconds: float,
            max_response_bytes: int) -> HttpResponse:
        self.calls += 1
        return HttpResponse(self.status, self.body, {})


def _row(day: date, *, close: str = "101.00", volume: int = 100) -> list[object]:
    stamp = datetime.combine(day, time(9, 15), ZoneInfo("Asia/Kolkata"))
    return [int(stamp.timestamp()), "100.00", "102.00", "99.00", close, volume]


def _provider(rows: list[list[object]], status: int = 200) -> tuple[
    FyersHistoricalDataProvider, FakeTransport,
]:
    transport = FakeTransport(rows, status)
    provider = FyersHistoricalDataProvider(
        transport=transport,
        app_id_getter=lambda: "FAKE-APP",
        token_getter=lambda: "fake-private-token",
        clock=lambda: datetime(2026, 11, 9, tzinfo=UTC),
    )
    return provider, transport


def _setup(tmp_path: Path, *, nse_price: bool = True) -> tuple[
    Path, Path, Path, ProvisionalIdentity,
]:
    canonical = tmp_path / "canonical.db"
    engine = create_engine(f"sqlite:///{canonical}")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        instrument = Instrument(symbol="TESTA", series="EQ", isin=ISIN)
        session.add(instrument)
        session.flush()
        if nse_price:
            batch = ImportBatch(
                file_name="synthetic-nse.zip", file_sha256="a" * 64,
                source_label="synthetic NSE", total_rows=1, imported_rows=1,
                skipped_rows=0, rejected_rows=0, status="completed",
            )
            session.add(batch)
            session.flush()
            session.add(DailyPrice(
                instrument_id=instrument.id, import_batch_id=batch.id,
                trade_date=FIRST, open_paise=10000, high_paise=10200,
                low_paise=9900, close_paise=10100, volume=100,
                price_basis="unadjusted",
            ))
        instrument_id = instrument.id
    engine.dispose()
    canonical.chmod(0o600)
    staging = tmp_path / "provisional.db"
    initialize_staging(staging)
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    identity = ProvisionalIdentity(
        "TESTA", "NSE:TESTA-EQ", ISIN, instrument_id,
        frozenset({FIRST}), "b" * 64,
    )
    return canonical, staging, raw, identity


def _calendar(*days: date) -> CalendarEvidence:
    return CalendarEvidence(ExchangeCalendar(tuple(
        ExchangeSession(day, time(9, 15), time(15, 30)) for day in days
    )), source_note="synthetic reviewed calendar")


def _stage(canonical: Path, staging: Path, raw: Path,
           identity: ProvisionalIdentity, provider: FyersHistoricalDataProvider,
           *, first: date = FIRST, last: date = LAST) -> ProvisionalResult:
    days = [FIRST, LAST]
    if last > LAST:
        days.append(last)
    return stage_fyers_daily_window(
        canonical, staging, provider, identity=identity, first=first, last=last,
        calendar=_calendar(*days), raw_dir=raw,
    )


def _staged_count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def _synthetic_nse_zip(directory: Path, day: date,
                       symbols: tuple[str, ...],
                       close: str = "101.00") -> tuple[str, str]:
    name = f"BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=HEADERS)
    writer.writeheader()
    for symbol in symbols:
        row = dict.fromkeys(HEADERS, "")
        row.update({
            "TradDt": day.isoformat(), "BizDt": day.isoformat(),
            "Sgmt": "CM", "Src": "NSE", "FinInstrmTp": "STK",
            "FinInstrmId": f"SYNTHETIC-{symbol}", "ISIN": ISIN,
            "TckrSymb": symbol, "SctySrs": "EQ",
            "OpnPric": "100.00", "HghPric": "102.00",
            "LwPric": "99.00", "ClsPric": close,
            "PrvsClsgPric": "100.00", "TtlTradgVol": "100",
        })
        writer.writerow(row)
    path = directory / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(name.removesuffix(".zip"), stream.getvalue())
    return name, hashlib.sha256(path.read_bytes()).hexdigest()


def test_provisional_match_stays_out_of_canonical_and_research(
    tmp_path: Path,
) -> None:
    canonical, staging, raw, identity = _setup(tmp_path)
    canonical_hash = hashlib.sha256(canonical.read_bytes()).hexdigest()
    provider, transport = _provider([_row(FIRST), _row(LAST)])
    result = _stage(canonical, staging, raw, identity, provider)
    assert transport.calls == 1
    assert result.candles == 2
    assert [row.state for row in result.reconciliation] == [
        "MATCH", "NO_NSE_REFERENCE",
    ]
    assert result.missing_nse_identity_dates == (LAST,)
    assert result.missing_sessions == ()
    assert _staged_count(staging, "provisional_runs") == 1
    assert _staged_count(staging, "provisional_candles") == 2
    with sqlite3.connect(staging) as connection:
        assert connection.execute(
            "SELECT DISTINCT identity_state FROM provisional_runs"
        ).fetchall() == [(IdentityState.PROVISIONAL.value,)]
        assert connection.execute(
            "SELECT close_paise FROM provisional_candles ORDER BY trade_date"
        ).fetchall() == [(10100,), (10100,)]
    engine = create_engine(f"sqlite:///{canonical}")
    with Session(engine) as session:
        assert session.scalar(select(DailyPrice.close_paise)) == 10100
        assert list(session.scalars(select(SourceObservation))) == []
        assert len(list(session.scalars(select(DailyPrice)))) == 1
    engine.dispose()
    assert hashlib.sha256(canonical.read_bytes()).hexdigest() == canonical_hash
    saved = next(raw.glob("provisional_*.json"))
    assert saved.stat().st_mode & 0o077 == 0
    assert "fake-private-token" not in saved.read_text()


def test_conflicting_nse_ohlcv_is_reported_without_overwrite(tmp_path: Path) -> None:
    canonical, staging, raw, identity = _setup(tmp_path)
    provider, _ = _provider([_row(FIRST, close="100.00", volume=101)])
    result = _stage(canonical, staging, raw, identity, provider)
    assert result.reconciliation[0].state == "CONFLICT"
    assert result.reconciliation[0].differences == {
        "close_paise": -100, "volume": 1,
    }
    with sqlite3.connect(canonical) as connection:
        close = connection.execute(
            "SELECT close_paise FROM daily_prices"
        ).fetchone()
        assert close == (10100,)


def test_hash_checked_local_nse_zip_reconciles_without_canonical_price(
    tmp_path: Path,
) -> None:
    canonical, staging, raw, identity = _setup(tmp_path, nse_price=False)
    name, digest = _synthetic_nse_zip(tmp_path, FIRST, (identity.symbol,))
    references = load_local_nse_references(
        tmp_path,
        [{"filename": name, "trade_date": FIRST.isoformat(), "sha256": digest}],
        {identity.symbol: identity},
    )
    provider, _ = _provider([_row(FIRST)])
    result = stage_fyers_daily_window(
        canonical, staging, provider, identity=identity, first=FIRST,
        last=LAST, calendar=_calendar(FIRST, LAST), raw_dir=raw,
        nse_references=references,
    )
    assert result.reconciliation[0].state == "MATCH"
    assert result.reconciliation[0].nse_identity_evidence
    assert _staged_count(staging, "provisional_candles") == 1
    with sqlite3.connect(canonical) as connection:
        assert connection.execute("SELECT count(*) FROM daily_prices").fetchone() == (
            0,
        )


def test_canonical_and_local_nse_reference_conflict_fails_offline(
    tmp_path: Path,
) -> None:
    canonical, _, _, identity = _setup(tmp_path)
    name, digest = _synthetic_nse_zip(
        tmp_path, FIRST, (identity.symbol,), close="100.00"
    )
    references = load_local_nse_references(
        tmp_path,
        [{"filename": name, "trade_date": FIRST.isoformat(), "sha256": digest}],
        {identity.symbol: identity},
    )
    with pytest.raises(FyersImportError, match="references conflict"):
        verify_nse_references(canonical, {identity.symbol: identity}, references)
    with pytest.raises(FyersImportError, match="hash/date differs"):
        load_local_nse_references(
            tmp_path,
            [{"filename": name, "trade_date": FIRST.isoformat(),
              "sha256": "0" * 64}],
            {identity.symbol: identity},
        )


def test_same_window_checkpoint_uses_raw_without_second_request(tmp_path: Path) -> None:
    canonical, staging, raw, identity = _setup(tmp_path)
    provider, transport = _provider([_row(FIRST)])
    first = _stage(canonical, staging, raw, identity, provider)
    second = _stage(canonical, staging, raw, identity, provider)
    assert first.fetched
    assert second.checkpoint_skipped and second.resumed_raw
    assert transport.calls == 1
    assert _staged_count(staging, "provisional_runs") == 1
    assert _staged_count(staging, "provisional_candles") == 1


def test_raw_response_can_resume_without_a_second_get(tmp_path: Path) -> None:
    canonical, staging, raw, identity = _setup(tmp_path)
    provider, transport = _provider([_row(FIRST)])
    instrument = identity.instrument()
    from app.modules.research.contracts import Timeframe

    source_ref = provider.request_url(instrument, Timeframe.DAY, FIRST, LAST)
    batch = provider.parse_response(
        transport.body, instrument, Timeframe.DAY, FIRST, LAST,
        retrieved_at_utc=datetime(2026, 11, 9, tzinfo=UTC), source_ref=source_ref,
    )
    _write_raw(
        _raw_path(raw, identity, FIRST, LAST), batch,
        {"source_ref": source_ref, "symbol": identity.symbol,
         "ticker": identity.ticker, "isin": identity.isin,
         "master_sha256": identity.master_sha256,
         "identity_state": "PROVISIONAL"},
    )
    result = _stage(canonical, staging, raw, identity, provider)
    assert result.resumed_raw and not result.fetched
    assert transport.calls == 0
    assert _staged_count(staging, "provisional_candles") == 1


def test_conflicting_provisional_redelivery_rolls_back(tmp_path: Path) -> None:
    canonical, staging, raw, identity = _setup(tmp_path)
    first_provider, _ = _provider([_row(FIRST), _row(LAST)])
    _stage(canonical, staging, raw, identity, first_provider)
    later = date(2026, 11, 2)
    second_provider, _ = _provider([
        _row(LAST, close="100.00"), _row(later),
    ])
    with pytest.raises(FyersImportError, match="conflicting provisional duplicate"):
        _stage(canonical, staging, raw, identity, second_provider,
               first=LAST, last=later)
    assert _staged_count(staging, "provisional_runs") == 1
    assert _staged_count(staging, "provisional_candles") == 2
    assert len(list(raw.glob("provisional_*.json"))) == 2


def test_invalid_internal_identity_fails_before_fetch(tmp_path: Path) -> None:
    canonical, staging, raw, identity = _setup(tmp_path)
    wrong = ProvisionalIdentity(
        identity.symbol, identity.ticker, "INE999A01010", identity.internal_id,
        identity.nse_evidence_dates, identity.master_sha256,
    )
    provider, transport = _provider([_row(FIRST)])
    with pytest.raises(FyersImportError, match="identity changed"):
        _stage(canonical, staging, raw, wrong, provider)
    assert transport.calls == 0
    assert _staged_count(staging, "provisional_runs") == 0


def test_insecure_staging_file_fails_before_fetch(tmp_path: Path) -> None:
    canonical, staging, raw, identity = _setup(tmp_path)
    staging.chmod(0o644)
    provider, transport = _provider([_row(FIRST)])
    with pytest.raises(FyersImportError, match="must be private"):
        _stage(canonical, staging, raw, identity, provider)
    assert transport.calls == 0


def test_missing_candle_is_reported_not_filled(tmp_path: Path) -> None:
    canonical, staging, raw, identity = _setup(tmp_path)
    provider, _ = _provider([_row(FIRST)])
    result = _stage(canonical, staging, raw, identity, provider)
    assert result.missing_sessions == (LAST,)
    assert _staged_count(staging, "provisional_candles") == 1


def test_provider_error_is_sanitized_and_writes_nothing(tmp_path: Path) -> None:
    canonical, staging, raw, identity = _setup(tmp_path)
    provider, transport = _provider([], status=403)
    with pytest.raises(ValueError) as error:
        _stage(canonical, staging, raw, identity, provider)
    assert transport.calls == 1
    assert "fake-private-token" not in str(error.value)
    assert _staged_count(staging, "provisional_runs") == 0
    assert list(raw.glob("provisional_*.json")) == []


def test_incomplete_daily_candle_is_not_staged(tmp_path: Path) -> None:
    canonical, staging, raw, identity = _setup(tmp_path)
    transport = FakeTransport([_row(LAST)])
    provider = FyersHistoricalDataProvider(
        transport=transport,
        app_id_getter=lambda: "FAKE-APP",
        token_getter=lambda: "fake-private-token",
        clock=lambda: datetime(2026, 10, 30, 10, tzinfo=UTC),
    )
    with pytest.raises(FyersImportError, match="incomplete provisional"):
        _stage(canonical, staging, raw, identity, provider)
    assert transport.calls == 1
    assert _staged_count(staging, "provisional_candles") == 0
    assert list(raw.glob("provisional_*.json")) == []


def test_evidence_cannot_claim_verified_mapping(tmp_path: Path) -> None:
    master = tmp_path / "master.json"
    master.write_text("{}")
    evidence = tmp_path / "evidence.json"
    from app.services.fyers_historical_provider import PILOT_SYMBOLS

    evidence.write_text(json.dumps({
        "fyers_current_master": {
            "sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
        },
        "nse_sources": [],
        "instruments": {
            symbol: {"nse_verified_dates": [],
                     "fyers_historical_identity_status": "unverified",
                     "verified_mapping_created": symbol == "TCS"}
            for symbol in PILOT_SYMBOLS
        },
    }))
    evidence.chmod(0o600)
    with pytest.raises(FyersImportError, match="must not claim verification"):
        _evidence(evidence, master)


def test_cli_requires_explicit_live_flags_without_network(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network attempt")

    monkeypatch.setattr(socket, "socket", forbidden)
    result = main([
        "stage-fyers-daily", "--database", "/private/tmp/none.db",
        "--staging-database", "/private/tmp/staging.db",
        "--raw-dir", "/private/tmp/none-raw",
        "--master-json", "missing", "--evidence-json", "missing",
        "--calendar-json", "missing", "--nse-zip-dir", "missing",
        "--symbol", "RELIANCE",
        "--from-date", "2026-10-12", "--to-date", "2026-11-06",
        "--max-requests", "1",
    ])
    assert result == 2
    assert "No FYERS request" in capsys.readouterr().out


def test_five_stock_provisional_preflight_never_reads_credentials_or_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from app.services.fyers_historical_provider import PILOT_SYMBOLS

    canonical = tmp_path / "canonical.db"
    engine = create_engine(f"sqlite:///{canonical}")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        for symbol in PILOT_SYMBOLS:
            session.add(Instrument(symbol=symbol, series="EQ", isin=ISIN))
    engine.dispose()
    with sqlite3.connect(canonical) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        connection.execute(
            "INSERT INTO alembic_version VALUES ('0004_market_data_foundation')"
        )
    canonical.chmod(0o600)
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    master = tmp_path / "master.json"
    master.write_text(json.dumps({
        f"NSE:{symbol}-EQ": {
            "exSymbol": symbol, "symTicker": f"NSE:{symbol}-EQ",
            "exSeries": "EQ", "exchangeName": "NSE", "isin": ISIN,
        } for symbol in PILOT_SYMBOLS
    }))
    name, digest = _synthetic_nse_zip(tmp_path, LAST, PILOT_SYMBOLS)
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({
        "fyers_current_master": {
            "sha256": hashlib.sha256(master.read_bytes()).hexdigest(),
        },
        "nse_sources": [{"filename": name, "trade_date": "2026-10-30",
                         "sha256": digest}],
        "instruments": {
            symbol: {
                "isin": ISIN, "fyers_current_ticker": f"NSE:{symbol}-EQ",
                "nse_verified_dates": ["2026-10-30"],
                "fyers_historical_identity_status": "unverified",
                "verified_mapping_created": False,
            } for symbol in PILOT_SYMBOLS
        },
    }))
    evidence.chmod(0o600)
    days = [date(2026, 10, 12) + timedelta(days=offset) for offset in range(26)]
    sessions = [day for day in days if day.weekday() < 5
                and day != date(2026, 10, 20)]
    calendar = tmp_path / "calendar.json"
    calendar.write_text(json.dumps({
        "source_note": "synthetic 19-session pilot calendar",
        "sessions": [
            {"date": day.isoformat(), "opens_local": "09:15:00",
             "closes_local": "15:30:00", "special": False}
            for day in sessions
        ],
        "holidays": ["2026-10-20"], "halts": [],
    }))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network attempt")

    monkeypatch.setattr(socket, "socket", forbidden)
    staging = tmp_path / "provisional.db"
    result = main([
        "stage-fyers-daily", "--database", str(canonical),
        "--staging-database", str(staging), "--raw-dir", str(raw),
        "--master-json", str(master), "--evidence-json", str(evidence),
        "--calendar-json", str(calendar), "--nse-zip-dir", str(tmp_path),
        "--symbol", "RELIANCE",
        "--from-date", "2026-10-12", "--to-date", "2026-11-06",
        "--max-requests", "1", "--preflight-only",
        "--app-config", str(tmp_path / "absent.env"),
        "--token-file", str(tmp_path / "absent-token"),
    ])
    assert result == 0
    output = capsys.readouterr().out
    assert "19 sessions; 5 local NSE reference rows; 18 dates" in output
    assert "Dates without NSE identity evidence: 2026-10-12" in output
    assert "2026-11-06" in output
    assert _staged_count(staging, "provisional_runs") == 0
    assert not list(raw.iterdir())
