"""Offline contract tests for the isolated Upstox historical adapter."""

import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime

import pytest

from app.services.upstox_historical_provider import (
    HistoricalTransport,
    HttpResponse,
    UpstoxAuthenticationError,
    UpstoxHistoricalDataProvider,
    UpstoxProviderConfig,
    UpstoxRateLimitError,
    UpstoxResponseError,
    UpstoxTransportError,
    UrllibTransport,
)

KEY = "NSE_EQ|INE009A01021"
FIRST = date(2026, 10, 26)
LAST = date(2026, 10, 30)
STAMP = "2026-10-30T00:00:00+05:30"
FAKE_TOKEN = "synthetic-test-token-never-used-for-network"
RETRIEVED = datetime(2026, 10, 31, 1, 2, tzinfo=UTC)


class FakeTransport(HistoricalTransport):
    """Scripted transport that cannot open a network socket."""

    def __init__(self, results: Sequence[HttpResponse | Exception]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, str], float, int]] = []

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> HttpResponse:
        self.calls.append((url, dict(headers), timeout_seconds, max_response_bytes))
        if not self.results:
            raise AssertionError("unexpected transport call")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _candle(
    *, timestamp: str = STAMP, opened: object = "100.01",
    high: object = "101.00", low: object = "99.99",
    close: object = "100.05", volume: object = 1234,
    open_interest: object = None, seven_fields: bool = False,
) -> list[object]:
    values = [timestamp, opened, high, low, close, volume]
    if seven_fields:
        values.append(open_interest)
    return values


def _body(candles: list[list[object]]) -> bytes:
    return json.dumps({"status": "success", "data": {"candles": candles}}).encode()


def _response(
    body: bytes | None = None,
    status: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HttpResponse:
    return HttpResponse(
        status, body if body is not None else _body([_candle()]), headers or {}
    )


def _provider(
    transport: HistoricalTransport,
    *,
    token_getter: Callable[[], str | None] | None = None,
    sleep: Callable[[float], None] | None = None,
    max_attempts: int = 3,
) -> UpstoxHistoricalDataProvider:
    return UpstoxHistoricalDataProvider(
        UpstoxProviderConfig(
            timeout_seconds=2.0, max_attempts=max_attempts,
            initial_backoff_seconds=0.25, max_calendar_days=10,
        ),
        transport=transport,
        token_getter=token_getter or (lambda: FAKE_TOKEN),
        clock=lambda: RETRIEVED,
        sleep=sleep or (lambda _: None),
        request_id_factory=lambda: "offline-request-id",
    )


@pytest.mark.parametrize("seven_fields", [False, True])
def test_success_parses_daily_candle_and_preserves_supplied_facts(
    seven_fields: bool,
) -> None:
    transport = FakeTransport([
        _response(_body([_candle(seven_fields=seven_fields, open_interest=0)]))
    ])
    batch = _provider(transport).fetch_daily(KEY, FIRST, LAST)
    assert len(batch.candles) == 1
    candle = batch.candles[0]
    assert candle.trade_date == LAST
    assert candle.timestamp_at_utc == datetime(2026, 10, 29, 18, 30, tzinfo=UTC)
    assert (
        candle.open_price, candle.high_price, candle.low_price,
        candle.close_price, candle.volume,
    ) == ("100.01", "101.00", "99.99", "100.05", 1234)
    assert candle.open_interest == (0 if seven_fields else None)
    assert ("open_interest" in candle.raw_payload) == seven_fields
    assert "previous_close" not in candle.raw_payload
    assert batch.retrieved_at_utc == RETRIEVED
    assert batch.request_id == "offline-request-id"
    assert len(batch.response_sha256) == 64
    assert transport.calls[0][1]["Authorization"] == f"Bearer {FAKE_TOKEN}"
    assert transport.calls[0][1]["X-Request-ID"] == "offline-request-id"
    assert transport.calls[0][2:] == (2.0, 2_000_000)


def test_json_numeric_prices_keep_exact_decimal_text() -> None:
    body = (
        b'{"status":"success","data":{"candles":'
        b'[["2026-10-30T00:00:00+05:30",100.01,101.00,99.99,100.05,1234]]}}'
    )
    batch = _provider(FakeTransport([])).parse_response(
        body, KEY, FIRST, LAST, retrieved_at_utc=RETRIEVED,
    )
    assert batch.candles[0].open_price == "100.01"
    assert batch.candles[0].close_price == "100.05"
    assert batch.candles[0].raw_payload["low"] == "99.99"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"{", "valid JSON"),
        (b"[]", "status is not success"),
        (b'{"status":"error","data":{"candles":[]}}', "status is not success"),
        (b'{"status":"success","data":{}}', "candle list is missing"),
        (_body([[STAMP, "100.00"]]), "6 or 7 fields"),
        (_body([_candle(timestamp="bad")]), "timestamp is malformed"),
        (_body([_candle(timestamp="2026-10-30T00:00:00")]), "lacks timezone"),
        (_body([_candle(timestamp="2026-11-01T00:00:00+05:30")]), "outside"),
        (_body([_candle(opened="-1.00")]), "open is invalid"),
        (_body([_candle(opened="100.001")]), "open is invalid"),
        (_body([_candle(low="102.00")]), "OHLC range is invalid"),
        (_body([_candle(close="102.00")]), "OHLC range is invalid"),
        (_body([_candle(volume=-1)]), "volume is invalid"),
        (_body([_candle(volume="100")]), "volume is invalid"),
        (_body([_candle(seven_fields=True, open_interest=-1)]), "open interest"),
        (_body([_candle(), _candle()]), "duplicate"),
    ],
)
def test_malformed_or_invalid_provider_data_fails_closed(
    body: bytes, message: str,
) -> None:
    with pytest.raises(UpstoxResponseError, match=message):
        _provider(FakeTransport([])).parse_response(
            body, KEY, FIRST, LAST, retrieved_at_utc=RETRIEVED,
        )


def test_token_loaded_at_request_time_and_missing_token_sends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UPSTOX_ANALYTICS_TOKEN", raising=False)
    transport = FakeTransport([_response()])
    provider = UpstoxHistoricalDataProvider(
        transport=transport, clock=lambda: RETRIEVED,
        sleep=lambda _: None, request_id_factory=lambda: "offline-request-id",
    )
    with pytest.raises(UpstoxAuthenticationError, match="unavailable"):
        provider.fetch_daily(KEY, FIRST, LAST)
    assert not transport.calls
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", FAKE_TOKEN)
    assert provider.fetch_daily(KEY, FIRST, LAST).candles[0].trade_date == LAST
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    ("status", "category"),
    [(401, "authentication rejected"), (403, "access forbidden")],
)
def test_authentication_status_is_sanitized_and_not_retried(
    status: int, category: str, caplog: pytest.LogCaptureFixture,
) -> None:
    body = json.dumps({
        "status": "error",
        "errors": [{"error_code": "UDAPI999999", "message": FAKE_TOKEN}],
    }).encode()
    transport = FakeTransport([_response(body=body, status=status)])
    sleeps: list[float] = []
    with pytest.raises(UpstoxAuthenticationError) as captured:
        _provider(transport, sleep=sleeps.append).fetch_daily(KEY, FIRST, LAST)
    error = captured.value
    assert error.status_code == status
    assert category in str(error)
    assert f"HTTP {status}" in str(error)
    assert error.documented_error_code is None
    assert "documented error code unavailable" in str(error)
    assert "UDAPI999999" not in str(error)
    assert FAKE_TOKEN not in str(error) + caplog.text
    assert len(transport.calls) == 1
    assert sleeps == []


def test_only_documented_historical_code_is_reported_without_server_message() -> None:
    body = json.dumps({
        "errors": [{"errorCode": "UDAPI1021", "message": FAKE_TOKEN}],
    }).encode()
    transport = FakeTransport([_response(body=body, status=403)])
    with pytest.raises(UpstoxAuthenticationError) as captured:
        _provider(transport).fetch_daily(KEY, FIRST, LAST)
    assert captured.value.documented_error_code == "UDAPI1021"
    assert "UDAPI1021" in str(captured.value)
    assert "historical request was rejected" in str(captured.value)
    assert FAKE_TOKEN not in str(captured.value)


@pytest.mark.parametrize(
    ("code", "explanation"),
    [
        ("UDAPI10000", "request URL was not recognized"),
        ("UDAPI100016", "credentials were rejected"),
        ("UDAPI100050", "token was rejected"),
        ("UDAPI100067", "endpoint is not permitted for an extended token"),
        ("UDAPI100073", "client ID is inactive"),
    ],
)
@pytest.mark.parametrize("status", [401, 403])
def test_documented_common_code_has_only_fixed_explanation(
    code: str, explanation: str, status: int, caplog: pytest.LogCaptureFixture,
) -> None:
    server_message = f"private server message {FAKE_TOKEN}"
    body = json.dumps({"errors": [{
        "error_code": code, "message": server_message,
        "property_path": server_message,
    }]}).encode()
    transport = FakeTransport([_response(body=body, status=status)])
    with pytest.raises(UpstoxAuthenticationError) as captured:
        _provider(transport).fetch_daily(KEY, FIRST, LAST)
    error = captured.value
    assert error.status_code == status
    assert error.documented_error_code == code
    assert f"HTTP {status}" in str(error)
    assert f"documented error code {code}: {explanation}" in str(error)
    assert server_message not in str(error) + caplog.text
    assert FAKE_TOKEN not in str(error) + caplog.text
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b'{"errors":"invalid"}',
        b'{"errors":[null,42,{"error_code":42}]}',
        b'{"errors":[{"error_code":"UDAPI999999","message":"private"}]}',
    ],
)
def test_malformed_or_unknown_authentication_response_is_sanitized(
    body: bytes,
) -> None:
    with pytest.raises(UpstoxAuthenticationError) as captured:
        _provider(FakeTransport([_response(body=body, status=403)])).fetch_daily(
            KEY, FIRST, LAST,
        )
    assert captured.value.status_code == 403
    assert captured.value.documented_error_code is None
    assert str(captured.value) == (
        "Upstox HTTP 403: access forbidden; documented error code unavailable"
    )


def test_oversized_authentication_body_still_reports_http_status() -> None:
    transport = FakeTransport([_response(body=b"x" * 2_000_001, status=401)])
    with pytest.raises(UpstoxAuthenticationError) as captured:
        _provider(transport).fetch_daily(KEY, FIRST, LAST)
    assert captured.value.status_code == 401
    assert captured.value.documented_error_code is None


def test_rate_limit_obeys_retry_after_and_stops_at_bound() -> None:
    sleeps: list[float] = []
    transport = FakeTransport([
        _response(status=429, headers={"Retry-After": "1.5"}), _response(),
    ])
    batch = _provider(transport, sleep=sleeps.append).fetch_daily(KEY, FIRST, LAST)
    assert batch.candles[0].trade_date == LAST
    assert len(transport.calls) == 2
    assert sleeps == [1.5]

    exhausted = FakeTransport([_response(status=429), _response(status=429)])
    sleeps.clear()
    with pytest.raises(UpstoxRateLimitError, match="rate limit persisted"):
        _provider(exhausted, sleep=sleeps.append, max_attempts=2).fetch_daily(
            KEY, FIRST, LAST,
        )
    assert len(exhausted.calls) == 2
    assert sleeps == [0.25]


def test_transport_timeout_is_sanitized_and_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def timeout(*_args: object, **_kwargs: object) -> None:
        raise TimeoutError("private transport detail")

    class TimeoutOpener:
        def open(self, *_args: object, **_kwargs: object) -> None:
            timeout()

    monkeypatch.setattr(
        "app.services.upstox_historical_provider.build_opener",
        lambda *_args: TimeoutOpener(),
    )
    with pytest.raises(UpstoxTransportError, match="Upstox request failed") as error:
        UrllibTransport().get("https://api.upstox.com/test", {}, 1.0, 1000)
    assert "private transport detail" not in str(error.value)

    sleeps: list[float] = []
    transport = FakeTransport([
        UpstoxTransportError("Upstox request failed"), _response(),
    ])
    _provider(transport, sleep=sleeps.append).fetch_daily(KEY, FIRST, LAST)
    assert len(transport.calls) == 2
    assert sleeps == [0.25]


def test_http_400_and_secret_echo_do_not_retry_or_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = FakeTransport([
        _response(body=f"server says {FAKE_TOKEN}".encode(), status=400),
    ])
    with pytest.raises(UpstoxResponseError) as error:
        _provider(transport).fetch_daily(KEY, FIRST, LAST)
    assert len(transport.calls) == 1
    assert FAKE_TOKEN not in str(error.value)
    assert FAKE_TOKEN not in caplog.text


def test_request_url_encoding_and_date_bounds() -> None:
    transport = FakeTransport([_response()])
    _provider(transport).fetch_daily(KEY, FIRST, LAST)
    assert transport.calls[0][0] == (
        "https://api.upstox.com/v3/historical-candle/"
        "NSE_EQ%7CINE009A01021/days/1/2026-10-30/2026-10-26"
    )
    invalid = _provider(FakeTransport([]))
    for key, start, end in (
        ("TESTA", FIRST, LAST),
        ("NSE_EQ|", FIRST, LAST),
        (KEY, LAST, FIRST),
        (KEY, FIRST, date(2026, 11, 10)),
    ):
        with pytest.raises(UpstoxResponseError):
            invalid.fetch_daily(key, start, end)
