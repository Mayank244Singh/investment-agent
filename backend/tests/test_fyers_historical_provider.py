"""FYERS history contracts use invented candles and an in-memory transport."""

import json
import socket
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request
from zoneinfo import ZoneInfo

import pytest

from app.cli import main
from app.db.models.prices import Instrument
from app.modules.research.contracts import Timeframe
from app.modules.research.evidence import (
    Exchange,
    IndianInstrument,
    validate_completed_candles,
)
from app.modules.research.intraday import ExchangeCalendar, ExchangeSession
from app.services import fyers_historical_provider as fyers_module
from app.services.fyers_historical_provider import (
    FYERS_USER_AGENT,
    FyersError,
    FyersHistoricalDataProvider,
    FyersInstrument,
    FyersTransport,
    read_access_token,
    verify_master_mapping,
)
from app.services.historical_transport import HttpResponse

DAY = date(2026, 10, 30)
STAMP = datetime(2026, 10, 30, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
NOW = datetime(2026, 10, 31, tzinfo=UTC)
MAPPED = FyersInstrument(
    "TESTA", "NSE:TESTA-EQ", "INE123A01012", 7, True,
    date(2026, 10, 26), date(2026, 10, 30),
)


class FakeTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, str], float, int]] = []

    def get(self, url: str, headers: Mapping[str, str], timeout_seconds: float,
            max_response_bytes: int) -> HttpResponse:
        self.calls.append((url, headers, timeout_seconds, max_response_bytes))
        return self.response


def body(rows: list[list[object]]) -> bytes:
    return json.dumps({"s": "ok", "code": 200, "candles": rows}).encode()


def row(at: datetime = STAMP, *, volume: object = 100,
        close: object = "101.00") -> list[object]:
    return [int(at.timestamp()), "100.00", "102.00", "99.00", close, volume]


def provider(
    response: HttpResponse,
) -> tuple[FyersHistoricalDataProvider, FakeTransport]:
    transport = FakeTransport(response)
    return (
        FyersHistoricalDataProvider(
            transport=transport, app_id_getter=lambda: "FAKE-100",
            token_getter=lambda: "synthetic-token", clock=lambda: NOW,
        ),
        transport,
    )


def test_daily_response_uses_paise_existing_validation_and_provenance() -> None:
    instance, transport = provider(HttpResponse(200, body([row()]), {}))
    batch = instance.fetch(MAPPED, Timeframe.DAY, DAY, DAY)
    assert len(transport.calls) == 1
    assert transport.calls[0][0] == (
        "https://api-t1.fyers.in/data/history?symbol=NSE%3ATESTA-EQ"
        "&resolution=D&date_format=1&range_from=2026-10-30"
        "&range_to=2026-10-30&cont_flag=0"
    )
    assert transport.calls[0][1]["Authorization"] == "FAKE-100:synthetic-token"
    assert transport.calls[0][1]["User-Agent"] == FYERS_USER_AGENT
    assert batch.candles[0].close_paise == 10_100
    assert batch.candles[0].is_complete
    observation, = batch.daily_observations()
    assert observation.provider == "fyers"
    assert observation.provider_instrument_key == "NSE:TESTA-EQ"
    assert observation.available_at_utc is None
    assert observation.retrieved_at_utc == NOW
    assert len(batch.response_sha256) == 64


def test_failed_pilot_window_uses_same_documented_query_format_as_short_check() -> None:
    instrument = FyersInstrument("RELIANCE", "NSE:RELIANCE-EQ")
    long_url = FyersHistoricalDataProvider.request_url(
        instrument, Timeframe.DAY, date(2021, 11, 10), date(2022, 11, 10)
    )
    short_url = FyersHistoricalDataProvider.request_url(
        instrument, Timeframe.DAY, date(2026, 10, 26), date(2026, 10, 30)
    )
    assert urlsplit(long_url).scheme == "https"
    assert urlsplit(long_url).netloc == "api-t1.fyers.in"
    assert urlsplit(long_url).path == "/data/history"
    long_query = parse_qs(urlsplit(long_url).query)
    short_query = parse_qs(urlsplit(short_url).query)
    assert long_query == {
        **short_query,
        "range_from": ["2021-11-10"],
        "range_to": ["2022-11-10"],
    }
    assert long_query["symbol"] == ["NSE:RELIANCE-EQ"]
    assert long_query["resolution"] == ["D"]
    assert long_query["date_format"] == ["1"]
    assert long_query["cont_flag"] == ["0"]


def test_daily_request_limit_accepts_366_inclusive_days_but_rejects_367() -> None:
    instrument = FyersInstrument("TESTA", "NSE:TESTA-EQ")
    first = date(2021, 11, 10)
    accepted = FyersHistoricalDataProvider.request_url(
        instrument, Timeframe.DAY, first, date(2022, 11, 10)
    )
    assert parse_qs(urlsplit(accepted).query)["range_to"] == ["2022-11-10"]
    with pytest.raises(FyersError, match="range exceeds"):
        FyersHistoricalDataProvider.request_url(
            instrument, Timeframe.DAY, first, date(2022, 11, 11)
        )


@pytest.mark.parametrize("timeframe,resolution", [
    (Timeframe.MINUTE_1, "1"),
    (Timeframe.MINUTE_5, "5"),
    (Timeframe.MINUTE_15, "15"),
])
def test_intraday_request_parameters_remain_documented(
    timeframe: Timeframe, resolution: str,
) -> None:
    instance, transport = provider(HttpResponse(200, body([row()]), {}))
    instance.fetch(MAPPED, timeframe, DAY, DAY)
    request = urlsplit(transport.calls[0][0])
    assert (request.scheme, request.netloc, request.path) == (
        "https", "api-t1.fyers.in", "/data/history",
    )
    assert parse_qs(request.query) == {
        "symbol": ["NSE:TESTA-EQ"], "resolution": [resolution],
        "date_format": ["1"], "range_from": ["2026-10-30"],
        "range_to": ["2026-10-30"], "cont_flag": ["0"],
    }


def test_urllib_transport_sends_explicit_user_agent_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str | None, str | None, float]] = []

    class FakeOpener:
        def open(self, request: Request, timeout: float) -> None:
            seen.append((request.get_header("User-agent"),
                         request.get_header("Authorization"), timeout))
            raise HTTPError(
                request.full_url, 403, "Forbidden", Message(), BytesIO(b""),
            )

    monkeypatch.setattr(fyers_module, "build_opener", lambda *args: FakeOpener())
    response = FyersTransport().get(
        "https://api-t1.fyers.in/data/history",
        {"User-Agent": FYERS_USER_AGENT,
         "Authorization": "FAKE-100:synthetic-token"}, 10.0, 100,
    )
    assert response.status_code == 403
    assert seen == [(FYERS_USER_AGENT, "FAKE-100:synthetic-token", 10.0)]


def test_intraday_evidence_remains_deferred_without_availability() -> None:
    instance, _ = provider(HttpResponse(200, body([row()]), {}))
    batch = instance.fetch(MAPPED, Timeframe.MINUTE_5, DAY, DAY)
    evidence, = batch.research_intraday_candles()
    assert evidence.start_utc == STAMP.astimezone(UTC)
    assert evidence.end_utc == evidence.start_utc + timedelta(minutes=5)
    assert evidence.available_at_utc is None
    assert evidence.adjustment_status == "unverified"
    assert evidence.identity_verified


def test_current_intraday_candle_is_not_complete() -> None:
    now = STAMP.astimezone(UTC) + timedelta(minutes=2)
    instance = FyersHistoricalDataProvider(
        app_id_getter=lambda: "FAKE-100", token_getter=lambda: "fake",
    )
    batch = instance.parse_response(
        body([row()]), MAPPED, Timeframe.MINUTE_5, DAY, DAY,
        retrieved_at_utc=now,
    )
    assert not batch.candles[0].is_complete


def test_missing_interval_and_unknown_availability_fail_evidence_gate() -> None:
    instance, _ = provider(HttpResponse(200, body([
        row(), row(STAMP + timedelta(minutes=10)),
    ]), {}))
    batch = instance.parse_response(
        body([row(), row(STAMP + timedelta(minutes=10))]),
        MAPPED, Timeframe.MINUTE_5, DAY, DAY, retrieved_at_utc=NOW,
    )
    calendar = ExchangeCalendar((
        ExchangeSession(DAY, time(9, 15), time(15, 30)),
    ))
    assessment = validate_completed_candles(
        batch.research_intraday_candles(),
        instrument=IndianInstrument(7, Exchange.NSE, "TESTA",
                                      "INE123A01012", True),
        calendar=calendar, calendar_exchange=Exchange.NSE,
        timeframe=Timeframe.MINUTE_5,
        window_start_utc=STAMP.astimezone(UTC),
        decision_at_utc=(STAMP + timedelta(minutes=15)).astimezone(UTC),
        max_age=timedelta(minutes=15),
    )
    assert "missing completed candle interval" in assessment.issues
    assert "candle availability unknown" in assessment.deferred
    assert not assessment.usable


@pytest.mark.parametrize("payload", [
    b"{", b"[]", b'{"s":"error","code":-300,"message":"SECRET"}',
    b'{"s":"ok"}', body([[1, 2]]),
    body([row(volume=-1)]), body([row(close="102.001")]),
    body([row(close="103.00")]), body([row(), row()]),
    body([row(STAMP + timedelta(minutes=5)), row()]),
])
def test_bad_or_duplicate_data_rejected_without_server_message(payload: bytes) -> None:
    instance, _ = provider(HttpResponse(200, payload, {}))
    with pytest.raises(FyersError) as caught:
        instance.fetch(MAPPED, Timeframe.MINUTE_5, DAY, DAY)
    assert "SECRET" not in str(caught.value)


@pytest.mark.parametrize("status,category", [
    (401, "authentication rejected"), (403, "access forbidden"),
    (422, "request failed"), (429, "rate limit"),
    (500, "request failed"),
])
def test_http_failures_are_sanitized_and_never_retried(
    status: int, category: str,
) -> None:
    instance, transport = provider(HttpResponse(
        status, b'{"message":"SECRET"}', {}
    ))
    with pytest.raises(FyersError, match=category) as caught:
        instance.fetch(MAPPED, Timeframe.DAY, DAY, DAY)
    assert caught.value.status_code == status
    assert "SECRET" not in str(caught.value)
    assert "synthetic-token" not in str(caught.value)
    if status == 403:
        assert "market-data permission denied" not in str(caught.value)
        assert "API edge" in str(caught.value)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("payload,expected_code", [
    (b'{"code":-50,"message":"SECRET"}', -50),
    (b'{"code":-999,"message":"SECRET"}', None),
    (b'{"code":"-50","message":"SECRET"}', None),
    (b'not-json SECRET', None),
])
def test_422_reports_only_allowlisted_code_without_leaking_response(
    payload: bytes, expected_code: int | None,
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str],
) -> None:
    instance, transport = provider(HttpResponse(422, payload, {}))
    with pytest.raises(FyersError) as caught:
        instance.fetch(MAPPED, Timeframe.DAY, DAY, DAY)
    assert caught.value.status_code == 422
    assert caught.value.provider_code == expected_code
    if expected_code == -50:
        assert "request parameter invalid" in str(caught.value)
    visible = str(caught.value) + caplog.text
    output = capsys.readouterr()
    visible += output.out + output.err
    assert all(secret not in visible for secret in (
        "SECRET", "FAKE-100", "synthetic-token",
    ))
    assert len(transport.calls) == 1


@pytest.mark.parametrize("code,expected", [
    (-8, "expired"), (-17, "invalid or expired"),
    (-360, "rate limit"), (-300, "symbol invalid"),
    (-352, "application identifier rejected"),
])
def test_documented_app_errors_use_fixed_non_sensitive_explanations(
    code: int, expected: str,
) -> None:
    payload = json.dumps({"s": "error", "code": code,
                          "message": "SECRET"}).encode()
    instance, _ = provider(HttpResponse(200, payload, {}))
    with pytest.raises(FyersError, match=expected) as caught:
        instance.fetch(MAPPED, Timeframe.DAY, DAY, DAY)
    assert caught.value.provider_code == code
    assert "SECRET" not in str(caught.value)


def test_403_with_documented_code_never_exposes_credentials(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str],
) -> None:
    payload = json.dumps({
        "s": "error", "code": -352,
        "message": "FAKE-100 synthetic-token SECRET",
    }).encode()
    instance, transport = provider(HttpResponse(403, payload, {}))
    with pytest.raises(FyersError) as caught:
        instance.fetch(MAPPED, Timeframe.DAY, DAY, DAY)
    assert caught.value.status_code == 403
    assert caught.value.provider_code == -352
    visible = str(caught.value) + caplog.text
    captured = capsys.readouterr()
    visible += captured.out + captured.err
    assert all(secret not in visible for secret in (
        "FAKE-100", "synthetic-token", "SECRET",
    ))
    assert len(transport.calls) == 1


def test_missing_credentials_send_no_request() -> None:
    transport = FakeTransport(HttpResponse(200, body([row()]), {}))
    instance = FyersHistoricalDataProvider(
        transport=transport, app_id_getter=lambda: None,
        token_getter=lambda: "fake",
    )
    with pytest.raises(FyersError, match="App ID"):
        instance.fetch(MAPPED, Timeframe.DAY, DAY, DAY)
    assert not transport.calls


def test_identity_and_range_fail_closed() -> None:
    with pytest.raises(FyersError, match="mapping"):
        FyersInstrument("TESTA", "NSE:OTHER-EQ")
    with pytest.raises(FyersError, match="mapping"):
        FyersInstrument("TESTA", "NSE:TESTA-EQ", identity_verified=True)
    instance, transport = provider(HttpResponse(200, body([row()]), {}))
    with pytest.raises(FyersError, match="range"):
        instance.fetch(MAPPED, Timeframe.MINUTE_1, date(2026, 1, 1), DAY)
    assert not transport.calls
    unverified = FyersInstrument("TESTA", "NSE:TESTA-EQ")
    batch = instance.parse_response(
        body([row()]), unverified, Timeframe.DAY, DAY, DAY,
        retrieved_at_utc=NOW,
    )
    with pytest.raises(FyersError, match="identity"):
        batch.daily_observations()


def test_mapping_matches_internal_isin_and_master_fields() -> None:
    internal = Instrument(id=7, symbol="TESTA", series="EQ", isin="INE123A01012")
    metadata: dict[str, object] = {
        "exSymbol": "TESTA", "symTicker": "NSE:TESTA-EQ",
        "exSeries": "EQ", "exchangeName": "NSE", "isin": "INE123A01012",
    }
    assert verify_master_mapping(
        internal, metadata, valid_from=date(2026, 10, 26), valid_to=DAY,
    ) == MAPPED
    metadata["isin"] = "INE123A01013"
    with pytest.raises(FyersError, match="does not match"):
        verify_master_mapping(
            internal, metadata, valid_from=date(2026, 10, 26), valid_to=DAY,
        )


def test_verified_mapping_cannot_be_used_outside_reviewed_dates() -> None:
    mapping = FyersInstrument(
        "TESTA", "NSE:TESTA-EQ", "INE123A01012", 7, True,
        DAY, DAY,
    )
    instance, transport = provider(HttpResponse(200, body([row()]), {}))
    with pytest.raises(FyersError, match="does not cover"):
        instance.fetch(mapping, Timeframe.DAY, date(2026, 10, 29), DAY)
    assert not transport.calls


def test_token_file_requires_private_regular_file(tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("synthetic-only\n")
    token_file.chmod(0o600)
    assert read_access_token(token_file) == "synthetic-only"
    token_file.chmod(0o644)
    with pytest.raises(FyersError, match="private"):
        read_access_token(token_file)
    token_file.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(token_file)
    with pytest.raises(FyersError, match="private"):
        read_access_token(link)


def test_cli_dry_run_touches_no_credentials_or_socket(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("network attempt")
    monkeypatch.setattr(socket, "socket", forbidden)
    result = main([
        "check-fyers-history", "--symbol", "RELIANCE",
        "--ticker", "NSE:RELIANCE-EQ",
        "--from-date", "2026-10-30", "--to-date", "2026-10-30",
    ])
    assert result == 2
    assert "No FYERS request made" in capsys.readouterr().out
