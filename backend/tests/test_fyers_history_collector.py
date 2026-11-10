"""Offline synthetic coverage for bounded, calendar-gated collection."""

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
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.cli import main
from app.db.base import Base
from app.db.models.prices import Instrument
from app.db.models.virtual import VirtualAccount
from app.modules.research.contracts import Timeframe
from app.modules.research.intraday import ExchangeCalendar, ExchangeSession
from app.modules.research.readiness import CalendarEvidence
from app.services.fyers_historical_provider import (
    FyersError,
    FyersHistoricalDataProvider,
)
from app.services.fyers_history_collector import (
    CollectionPlan,
    RequestWindow,
    UniverseEntry,
    _retryable_error,
    collect,
    date_windows,
    load_universe,
    prepare_plan,
)
from app.services.fyers_import_service import _expected_days
from app.services.fyers_provisional_service import (
    ProvisionalIdentity,
    _raw_path,
    _write_raw,
    initialize_staging,
)
from app.services.historical_transport import HttpResponse
from app.services.price_import_service import HEADERS

FIRST = date(2026, 10, 29)
LAST = date(2026, 10, 30)
ISIN = "INE123A01012"
TOKEN = "synthetic-token-must-not-appear"


def _bar(day: date, *, close: str = "101.00") -> list[object]:
    at = datetime.combine(day, time(9, 15), ZoneInfo("Asia/Kolkata"))
    return [int(at.timestamp()), "100.00", "102.00", "99.00", close, 100]


class FakeTransport:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self.responses = responses
        self.calls = 0
        self.headers: list[Mapping[str, str]] = []

    def get(self, url: str, headers: Mapping[str, str], timeout_seconds: float,
            max_response_bytes: int) -> HttpResponse:
        self.headers.append(headers)
        response = self.responses[self.calls]
        self.calls += 1
        return response


def _response(*rows: list[object], status: int = 200) -> HttpResponse:
    return HttpResponse(
        status, json.dumps({"s": "ok", "code": 200, "candles": rows}).encode(), {}
    )


def _provider(*responses: HttpResponse) -> tuple[FyersHistoricalDataProvider,
                                                 FakeTransport]:
    transport = FakeTransport(list(responses))
    provider = FyersHistoricalDataProvider(
        transport=transport, app_id_getter=lambda: "FAKE-APP",
        token_getter=lambda: TOKEN,
        clock=lambda: datetime(2026, 11, 9, tzinfo=UTC),
    )
    return provider, transport


def _plan(tmp_path: Path, *, first: date = FIRST,
          last: date = LAST) -> CollectionPlan:
    tmp_path.chmod(0o700)
    canonical = tmp_path / "canonical.db"
    engine = create_engine(f"sqlite:///{canonical}")
    Base.metadata.create_all(engine)
    with Session(engine) as session, session.begin():
        session.add(Instrument(symbol="TESTA", series="EQ", isin=ISIN))
        session.add(VirtualAccount(
            id=1, currency="INR", initial_capital_paise=1_000_000,
            fixture_sha256="f" * 64, seeded_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
        ))
    engine.dispose()
    canonical.chmod(0o600)
    staging = tmp_path / "provisional.db"
    initialize_staging(staging)
    raw = tmp_path / "raw"
    raw.mkdir(mode=0o700)
    identity = ProvisionalIdentity(
        "TESTA", "NSE:TESTA-EQ", ISIN, 1, frozenset(), "a" * 64,
    )
    days = tuple(sorted({FIRST, LAST, first, last}))
    calendar = CalendarEvidence(
        ExchangeCalendar(tuple(
            ExchangeSession(day, time(9, 15), time(15, 30)) for day in days
        )),
        source_note="synthetic reviewed calendar",
    )
    window = RequestWindow("TESTA", first, last, "request-1")
    return CollectionPlan(
        canonical, staging, raw,
        (UniverseEntry("TESTA", "NSE:TESTA-EQ", ISIN, "instruments.TESTA"),),
        {"TESTA": identity}, calendar, {}, (window,),
        "a" * 64, "b" * 64, "c" * 64,
    )


def _count(path: Path, table: str) -> int:
    with sqlite3.connect(path) as connection:
        return int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])


def test_five_year_windows_are_contiguous_and_include_leap_day() -> None:
    windows = date_windows(date(2021, 11, 10), date(2026, 11, 9))
    assert len(windows) == 5
    assert windows[0] == (date(2021, 11, 10), date(2022, 11, 10))
    assert windows[-1] == (date(2025, 11, 13), date(2026, 11, 9))
    assert sum((end - start).days + 1 for start, end in windows) == 1826
    assert all((end - start).days < 366 for start, end in windows)
    assert date_windows(date(2024, 2, 29), date(2024, 2, 29)) == (
        (date(2024, 2, 29), date(2024, 2, 29)),
    )


def test_five_year_windows_cover_each_synthetic_session_once() -> None:
    first, last = date(2021, 11, 10), date(2026, 11, 9)
    windows = date_windows(first, last)
    covered = [start + timedelta(days=offset)
               for start, end in windows
               for offset in range((end - start).days + 1)]
    expected = [first + timedelta(days=offset)
                for offset in range((last - first).days + 1)]
    assert covered == expected
    assert date(2024, 2, 29) in covered
    assert len(set(covered)) == len(covered)


@pytest.mark.parametrize("status,code,expected", [
    (422, -50, False), (422, -360, False),
    (401, -8, False), (403, -352, False),
    (429, -360, True), (503, None, True),
    (None, -360, True), (None, None, False),
])
def test_only_transient_provider_errors_are_retryable(
    status: int | None, code: int | None, expected: bool,
) -> None:
    error = FyersError("sanitized failure", status_code=status,
                       provider_code=code)
    assert _retryable_error(error) is expected


def test_universe_rejects_duplicate_or_assumed_ticker(tmp_path: Path) -> None:
    path = tmp_path / "universe.json"
    entry = {"symbol": "TESTA", "ticker": "NSE:TESTA-EQ", "isin": ISIN,
             "evidence_ref": "instruments.TESTA"}
    path.write_text(json.dumps({"version": 1, "instruments": [entry, entry]}))
    with pytest.raises(ValueError, match="duplicate"):
        load_universe(path)
    entry["ticker"] = "NSE:OTHER-EQ"
    path.write_text(json.dumps({"version": 1, "instruments": [entry]}))
    with pytest.raises(ValueError, match="invalid"):
        load_universe(path)


def test_valid_collection_and_resume_do_not_touch_canonical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _plan(tmp_path)
    before = hashlib.sha256(plan.canonical.read_bytes()).hexdigest()
    provider, transport = _provider(_response(_bar(FIRST), _bar(LAST)))
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("network socket opened")))
    report = collect(
        plan, provider, max_requests=1, resume=False, sleeper=lambda _: None
    )
    assert transport.calls == 1
    assert report["completed_windows"] == 1
    assert report["distinct_trading_sessions"] == 2
    assert report["research_eligible"] is False
    assert report["corporate_action_status"] == "UNVERIFIED"
    assert _count(plan.staging, "provisional_runs") == 1
    assert _count(plan.staging, "provisional_candles") == 2
    assert hashlib.sha256(plan.canonical.read_bytes()).hexdigest() == before
    assert Path(str(report["snapshot"])).stat().st_mode & 0o077 == 0
    raw_file = next(plan.raw_dir.glob("provisional_*.json"))
    assert TOKEN not in raw_file.read_text()
    resumed = collect(plan, provider, max_requests=1, resume=True,
                      sleeper=lambda _: None)
    assert resumed["requests"] == 0
    assert transport.calls == 1
    assert _count(plan.staging, "provisional_candles") == 2


def test_empty_response_reports_missing_sessions(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    provider, _ = _provider(_response())
    report = collect(plan, provider, max_requests=1, resume=False,
                     sleeper=lambda _: None)
    assert report["failures"]
    windows = report["windows"]
    assert isinstance(windows, list)
    assert isinstance(windows[0], dict)
    assert windows[0]["missing_sessions"] == [
        FIRST.isoformat(), LAST.isoformat(),
    ]
    assert _count(plan.staging, "provisional_candles") == 0


def test_malformed_response_stops_without_staging(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    provider, transport = _provider(HttpResponse(200, b"not-json", {}))
    report = collect(plan, provider, max_requests=1, resume=False,
                     sleeper=lambda _: None)
    assert transport.calls == 1
    assert report["failures"]
    assert _count(plan.staging, "provisional_runs") == 0
    assert not list(plan.raw_dir.glob("provisional_*.json"))


def test_invalid_ohlc_is_rejected_before_staging(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    invalid = _bar(FIRST)
    invalid[3] = "103.00"  # low exceeds high
    provider, _ = _provider(_response(invalid))
    report = collect(plan, provider, max_requests=1, resume=False,
                     sleeper=lambda _: None)
    assert report["failures"]
    assert _count(plan.staging, "provisional_runs") == 0


def test_authentication_failure_never_retries_or_leaks_token(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    provider, transport = _provider(HttpResponse(401, b'{"code":-8}', {}))
    report = collect(plan, provider, max_requests=3, resume=False,
                     sleeper=lambda _: None)
    assert transport.calls == 1
    assert report["requests"] == 1
    assert "Refresh the FYERS access token manually" in str(report["failures"])
    assert TOKEN not in json.dumps(report)
    assert _count(plan.staging, "provisional_candles") == 0


def test_missing_credential_counts_zero_http_requests(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    transport = FakeTransport([])

    def missing_token() -> str:
        raise FyersError("FYERS token file is unavailable")

    provider = FyersHistoricalDataProvider(
        transport=transport, app_id_getter=lambda: "FAKE-APP",
        token_getter=missing_token,
    )
    report = collect(plan, provider, max_requests=1, resume=False,
                     sleeper=lambda _: None)
    assert transport.calls == 0
    assert report["requests"] == 0
    assert report["failures"]
    assert _count(plan.staging, "provisional_runs") == 0


def test_rate_limit_retries_with_budget_and_backoff(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    provider, transport = _provider(
        HttpResponse(429, b'{"code":-429}', {}),
        _response(_bar(FIRST), _bar(LAST)),
    )
    waits: list[float] = []
    report = collect(plan, provider, max_requests=2, resume=False,
                     sleeper=waits.append)
    assert transport.calls == 2
    assert report["requests"] == 2
    assert not report["failures"]
    assert waits == [1.0, 1.3]


def test_request_budget_counts_retry_and_requires_resume(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    provider, transport = _provider(HttpResponse(429, b'{"code":-429}', {}))
    report = collect(plan, provider, max_requests=1, resume=False,
                     sleeper=lambda _: None)
    assert transport.calls == 1
    assert report["completed_windows"] == 0
    assert report["failures"]


def test_conflicting_redelivery_retains_original_staging(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    provider, _ = _provider(_response(_bar(FIRST), _bar(LAST)))
    collect(plan, provider, max_requests=1, resume=False, sleeper=lambda _: None)
    conflicting = _plan_conflicting_window(plan)
    changed, _ = _provider(_response(_bar(FIRST, close="100.00")))
    report = collect(conflicting, changed, max_requests=1, resume=False,
                     sleeper=lambda _: None)
    assert report["failures"]
    assert _count(plan.staging, "provisional_runs") == 1
    assert _count(plan.staging, "provisional_candles") == 2


def test_raw_checkpoint_after_interruption_resumes_without_get(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)
    provider, transport = _provider()
    identity = plan.identities["TESTA"]
    source_ref = provider.request_url(
        identity.instrument(), Timeframe.DAY, FIRST, LAST,
    )
    body = _response(_bar(FIRST), _bar(LAST)).body
    batch = provider.parse_response(
        body, identity.instrument(), Timeframe.DAY, FIRST, LAST,
        retrieved_at_utc=datetime(2026, 11, 9, tzinfo=UTC),
        source_ref=source_ref,
    )
    _write_raw(_raw_path(plan.raw_dir, identity, FIRST, LAST), batch, {
        "source_ref": source_ref, "symbol": "TESTA",
        "ticker": "NSE:TESTA-EQ", "isin": ISIN,
        "master_sha256": "a" * 64, "identity_state": "PROVISIONAL",
    })
    report = collect(plan, provider, max_requests=1, resume=True,
                     sleeper=lambda _: None)
    assert report["requests"] == 0
    assert transport.calls == 0
    assert _count(plan.staging, "provisional_runs") == 1
    assert _count(plan.staging, "provisional_candles") == 2


def _plan_conflicting_window(plan: CollectionPlan) -> CollectionPlan:
    return CollectionPlan(
        plan.canonical, plan.staging, plan.raw_dir, plan.entries,
        plan.identities, plan.calendar, plan.references,
        (RequestWindow("TESTA", FIRST, FIRST, "request-2"),),
        plan.master_sha256, plan.calendar_sha256, plan.universe_sha256,
    )


def _preflight_inputs(tmp_path: Path) -> dict[str, Path]:
    plan = _plan(tmp_path, first=FIRST, last=FIRST)
    with sqlite3.connect(plan.canonical) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num TEXT NOT NULL)")
        connection.execute(
            "INSERT INTO alembic_version VALUES ('0004_market_data_foundation')"
        )
    master = tmp_path / "master.json"
    master.write_text(json.dumps({"NSE:TESTA-EQ": {
        "exSymbol": "TESTA", "symTicker": "NSE:TESTA-EQ",
        "exSeries": "EQ", "exchangeName": "NSE", "isin": ISIN,
    }}))
    master.chmod(0o600)
    master_sha = hashlib.sha256(master.read_bytes()).hexdigest()
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"version": 1, "instruments": [{
        "symbol": "TESTA", "ticker": "NSE:TESTA-EQ", "isin": ISIN,
        "evidence_ref": "instruments.TESTA",
    }]}))
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=HEADERS)
    writer.writeheader()
    row = dict.fromkeys(HEADERS, "")
    row.update({
        "TradDt": FIRST.isoformat(), "BizDt": FIRST.isoformat(),
        "Sgmt": "CM", "Src": "NSE", "FinInstrmTp": "STK",
        "FinInstrmId": "SYNTHETIC-TESTA", "ISIN": ISIN,
        "TckrSymb": "TESTA", "SctySrs": "EQ",
        "OpnPric": "100.00", "HghPric": "102.00",
        "LwPric": "99.00", "ClsPric": "101.00",
        "PrvsClsgPric": "100.00", "TtlTradgVol": "100",
    })
    writer.writerow(row)
    zip_name = f"BhavCopy_NSE_CM_0_0_0_{FIRST:%Y%m%d}_F_0000.csv.zip"
    zip_path = tmp_path / zip_name
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(zip_name.removesuffix(".zip"), stream.getvalue())
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({
        "fyers_current_master": {"sha256": master_sha},
        "instruments": {"TESTA": {
            "isin": ISIN, "fyers_current_ticker": "NSE:TESTA-EQ",
            "verified_mapping_created": False,
        }},
        "nse_sources": [{
            "trade_date": FIRST.isoformat(), "filename": zip_name,
            "sha256": hashlib.sha256(zip_path.read_bytes()).hexdigest(),
        }],
    }))
    evidence.chmod(0o600)
    calendar = tmp_path / "calendar.json"
    calendar.write_text(json.dumps({
        "source_note": "synthetic exchange calendar",
        "sessions": [{"date": FIRST.isoformat(), "opens_local": "09:15:00",
                      "closes_local": "15:30:00", "special": False}],
        "holidays": [], "halts": [],
    }))
    return {
        "canonical": plan.canonical, "staging": plan.staging,
        "raw_dir": plan.raw_dir, "universe_file": universe,
        "master_file": master, "evidence_file": evidence,
        "calendar_file": calendar, "nse_zip_dir": tmp_path,
    }


def test_full_preflight_is_read_only_and_requires_calendar(tmp_path: Path) -> None:
    paths = _preflight_inputs(tmp_path)
    before = {name: hashlib.sha256(paths[name].read_bytes()).hexdigest()
              for name in ("canonical", "staging")}
    prepared = prepare_plan(
        **paths, supplemental_manifest=None, first=FIRST, last=FIRST,
    )
    assert len(prepared.windows) == 1
    assert len(prepared.references) == 1
    assert prepared.identities["TESTA"].instrument().identity_verified is False
    assert before == {name: hashlib.sha256(paths[name].read_bytes()).hexdigest()
                      for name in before}
    with pytest.raises(ValueError, match="calendar does not classify"):
        prepare_plan(
            **paths, supplemental_manifest=None, first=FIRST, last=LAST,
        )


def test_five_year_preflight_requires_explicit_weekend_classification(
    tmp_path: Path,
) -> None:
    paths = _preflight_inputs(tmp_path)
    calendar_path = paths["calendar_file"]
    calendar = json.loads(calendar_path.read_text())
    calendar["sessions"].append({
        "date": LAST.isoformat(), "opens_local": "09:15:00",
        "closes_local": "15:30:00", "special": False,
    })
    calendar["holidays"] = ["2026-10-31"]
    calendar_path.write_text(json.dumps(calendar))
    with pytest.raises(ValueError, match="calendar does not classify 2026-11-01"):
        prepare_plan(
            **paths, supplemental_manifest=None,
            first=FIRST, last=date(2026, 11, 1),
        )


def test_reviewed_weekend_session_and_holiday_are_distinct() -> None:
    calendar = CalendarEvidence(
        ExchangeCalendar(
            (
                ExchangeSession(date(2026, 11, 6), time(9, 15), time(15, 30)),
                ExchangeSession(date(2026, 11, 8), time(14, 20), time(15, 35), True),
                ExchangeSession(date(2026, 11, 9), time(9, 15), time(15, 30)),
            ),
            frozenset({date(2026, 11, 7)}),
        ),
        source_note="synthetic test for source-reviewed session classification",
    )
    assert _expected_days(
        calendar, date(2026, 11, 6), date(2026, 11, 9)
    ) == (date(2026, 11, 6), date(2026, 11, 8), date(2026, 11, 9))


def test_preflight_rejects_conflicting_current_identity(tmp_path: Path) -> None:
    paths = _preflight_inputs(tmp_path)
    master = paths["master_file"]
    payload = json.loads(master.read_text())
    payload["NSE:TESTA-EQ"]["isin"] = "INE999A01012"
    master.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="master and identity evidence differ"):
        prepare_plan(
            **paths, supplemental_manifest=None, first=FIRST, last=FIRST,
        )


def test_cli_preflight_never_reads_token_or_writes_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _preflight_inputs(tmp_path)
    before = {name: hashlib.sha256(paths[name].read_bytes()).hexdigest()
              for name in ("canonical", "staging")}
    monkeypatch.setattr(
        "app.services.fyers_historical_provider.read_access_token",
        lambda _: (_ for _ in ()).throw(AssertionError("token read")),
    )
    args = [
        "collect-fyers-history", "--universe-file", str(paths["universe_file"]),
        "--from-date", FIRST.isoformat(), "--to-date", FIRST.isoformat(),
        "--database", str(paths["canonical"]),
        "--staging-database", str(paths["staging"]),
        "--raw-dir", str(paths["raw_dir"]),
        "--master-json", str(paths["master_file"]),
        "--evidence-json", str(paths["evidence_file"]),
        "--calendar-json", str(paths["calendar_file"]),
        "--nse-zip-dir", str(paths["nse_zip_dir"]),
        "--max-requests", "1", "--preflight-only",
    ]
    assert main(args) == 0
    assert before == {name: hashlib.sha256(paths[name].read_bytes()).hexdigest()
                      for name in before}
    assert not list(paths["raw_dir"].iterdir())


def test_network_failure_is_bounded_and_not_retried_forever(
    tmp_path: Path,
) -> None:
    plan = _plan(tmp_path)

    class FailingTransport:
        calls = 0

        def get(self, url: str, headers: Mapping[str, str],
                timeout_seconds: float, max_response_bytes: int) -> HttpResponse:
            self.calls += 1
            raise FyersError("FYERS network request failed")

    transport = FailingTransport()
    provider = FyersHistoricalDataProvider(
        transport=transport, app_id_getter=lambda: "FAKE-APP",
        token_getter=lambda: TOKEN,
    )
    report = collect(plan, provider, max_requests=3, resume=False,
                     sleeper=lambda _: None)
    assert transport.calls == 3
    assert report["requests"] == 3
    assert report["failures"]
    assert TOKEN not in json.dumps(report)
