"""Bounded FYERS v3 history adapter. Parsing is offline; no data is persisted."""

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
from zoneinfo import ZoneInfo

from app.db.models.prices import Instrument
from app.modules.research.contracts import Timeframe
from app.modules.research.intraday import IntradayCandle
from app.services.historical_transport import HistoricalTransport, HttpResponse
from app.services.market_data_service import (
    MarketDataError,
    NormalizedObservation,
    ObservationInput,
    decimal_paise,
    normalize_observation,
)

INDIA = ZoneInfo("Asia/Kolkata")
HISTORY_URL = "https://api-t1.fyers.in/data/history"
FYERS_USER_AGENT = "Mozilla/5.0 (compatible; InvestmentAgent/1.0; personal-research)"
TOKEN_FILE = Path.home() / "fyers-auth" / ".access_token"
PILOT_SYMBOLS = ("RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK")
RESOLUTIONS = {
    Timeframe.DAY: "D",
    Timeframe.MINUTE_1: "1",
    Timeframe.MINUTE_5: "5",
    Timeframe.MINUTE_15: "15",
}
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]\Z")
_SYMBOL = re.compile(r"[A-Z0-9&._-]{1,20}\Z")
_SAFE_CODES = {
    -8: "access token expired",
    -15: "access token invalid",
    -16: "token authentication failed",
    -17: "access token invalid or expired",
    -50: "request parameter invalid",
    -300: "instrument symbol invalid",
    -352: "application identifier rejected for this request",
    -360: "plan rate limit exceeded",
    -429: "API rate limit exceeded",
}


class FyersError(ValueError):
    """Safe diagnostic; never includes request headers or provider messages."""

    def __init__(self, message: str, *, status_code: int | None = None,
                 provider_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider_code = provider_code


@dataclass(frozen=True)
class FyersInstrument:
    """Explicit symbol-master identity; a ticker alone proves no ISIN history."""

    symbol: str
    ticker: str
    isin: str | None = None
    internal_id: int | None = None
    identity_verified: bool = False
    valid_from: date | None = None
    valid_to: date | None = None

    def __post_init__(self) -> None:
        if (
            not _SYMBOL.fullmatch(self.symbol)
            or self.ticker != f"NSE:{self.symbol}-EQ"
            or (self.isin is not None and not _ISIN.fullmatch(self.isin))
            or (self.internal_id is not None and (
                type(self.internal_id) is not int or self.internal_id <= 0
            ))
            or (self.identity_verified and (
                self.isin is None or self.internal_id is None
                or self.valid_from is None or self.valid_to is None
            ))
            or (self.valid_from is not None and self.valid_to is not None
                and self.valid_from > self.valid_to)
        ):
            raise FyersError("NSE EQ identity needs an explicit, matching mapping")


def verify_master_mapping(
    instrument: Instrument, master_record: Mapping[str, object],
    *, valid_from: date, valid_to: date,
) -> FyersInstrument:
    """Require independent review of ticker validity over a bounded interval."""
    symbol = instrument.symbol
    isin = instrument.isin
    if (
        instrument.series != "EQ" or isin is None
        or master_record.get("exSymbol") != symbol
        or master_record.get("symTicker") != f"NSE:{symbol}-EQ"
        or master_record.get("exSeries") != "EQ"
        or master_record.get("exchangeName") != "NSE"
        or master_record.get("isin") != isin
    ):
        raise FyersError("FYERS symbol-master identity does not match instrument")
    return FyersInstrument(
        symbol, f"NSE:{symbol}-EQ", isin, instrument.id, True,
        valid_from, valid_to,
    )


@dataclass(frozen=True)
class FyersCandle:
    start_utc: datetime
    trade_date: date
    open_paise: int
    high_paise: int
    low_paise: int
    close_paise: int
    volume: int
    is_complete: bool
    source_record_key: str
    raw_payload: Mapping[str, str] = field(repr=False)


@dataclass(frozen=True)
class FyersBatch:
    instrument: FyersInstrument
    timeframe: Timeframe
    from_date: date
    to_date: date
    candles: tuple[FyersCandle, ...]
    retrieved_at_utc: datetime
    response_sha256: str
    source_ref: str
    raw_response: bytes = field(repr=False)

    def daily_observations(self) -> tuple[NormalizedObservation, ...]:
        """Use existing paise/OHLC rules; availability is deliberately unknown."""
        if self.timeframe is not Timeframe.DAY:
            raise FyersError("daily observations require daily candles")
        if not self.instrument.identity_verified:
            raise FyersError("instrument identity is not verified")
        if (
            self.instrument.valid_from is None or self.instrument.valid_to is None
            or self.from_date < self.instrument.valid_from
            or self.to_date > self.instrument.valid_to
        ):
            raise FyersError("instrument mapping does not cover requested history")
        results: list[NormalizedObservation] = []
        for bar in self.candles:
            if not bar.is_complete:
                continue
            try:
                results.append(normalize_observation(ObservationInput(
                    provider="fyers",
                    source_record_key=bar.source_record_key,
                    provider_instrument_key=self.instrument.ticker,
                    symbol=self.instrument.symbol,
                    exchange="NSE",
                    series="EQ",
                    trade_date=bar.trade_date,
                    open_price=Decimal(bar.open_paise) / 100,
                    high_price=Decimal(bar.high_paise) / 100,
                    low_price=Decimal(bar.low_paise) / 100,
                    close_price=Decimal(bar.close_paise) / 100,
                    volume=bar.volume,
                    event_at_utc=bar.start_utc,
                    retrieved_at_utc=self.retrieved_at_utc,
                    available_at_utc=None,
                )))
            except MarketDataError as exc:
                raise FyersError(
                    "FYERS candle fails source observation validation"
                ) from exc
        return tuple(results)

    def research_intraday_candles(self) -> tuple[IntradayCandle, ...]:
        """Phase 7D.1 evidence remains deferred until availability is verified."""
        if self.timeframe is Timeframe.DAY or self.instrument.internal_id is None:
            raise FyersError("intraday evidence needs a mapped internal instrument")
        duration = timedelta(minutes=self.timeframe.minutes)
        return tuple(IntradayCandle(
            instrument_id=self.instrument.internal_id,
            timeframe=self.timeframe,
            start_utc=bar.start_utc,
            end_utc=bar.start_utc + duration,
            available_at_utc=None,
            is_complete=bar.is_complete,
            open_paise=bar.open_paise,
            high_paise=bar.high_paise,
            low_paise=bar.low_paise,
            close_paise=bar.close_paise,
            volume=bar.volume,
            provider="fyers",
            source_record_key=bar.source_record_key,
            adjustment_status="unverified",
            identity_verified=self.instrument.identity_verified,
        ) for bar in self.candles)


class FyersTransport:
    """One GET, no proxies, redirects or retries; bounded response."""

    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, request: Request, fp: object, code: int,
                             msg: str, headers: object, newurl: str) -> None:
            return None

    def get(self, url: str, headers: Mapping[str, str], timeout_seconds: float,
            max_response_bytes: int) -> HttpResponse:
        request = Request(url, headers=dict(headers), method="GET")
        try:
            with build_opener(ProxyHandler({}), self._NoRedirect()).open(
                request, timeout=timeout_seconds
            ) as response:
                return HttpResponse(
                    response.status, response.read(max_response_bytes + 1),
                    dict(response.headers),
                )
        except HTTPError as response:
            return HttpResponse(
                response.code, response.read(max_response_bytes + 1),
                dict(response.headers),
            )
        except (URLError, TimeoutError, OSError):
            raise FyersError("FYERS network request failed") from None


def read_access_token(path: Path = TOKEN_FILE) -> str:
    """Read a private regular file only at request time; never disclose content."""
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or metadata.st_size > 8192
        ):
            raise FyersError("FYERS token file must be private and regular")
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as file:
            opened = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_uid != os.getuid()
                or opened.st_mode & 0o077
            ):
                raise FyersError("FYERS token file must be private and regular")
            content = file.read(8193)
        if len(content) > 8192:
            raise FyersError("FYERS token file is too large")
        token = content.decode("utf-8").strip()
    except (OSError, UnicodeError):
        raise FyersError("FYERS token file is unavailable") from None
    if not token or any(char.isspace() for char in token):
        raise FyersError("FYERS token file is invalid")
    return token


class FyersHistoricalDataProvider:
    """Parse bounded NSE EQ daily/1m/5m/15m candles; never writes to a DB."""

    def __init__(self, *, transport: HistoricalTransport | None = None,
                 app_id_getter: Callable[[], str | None] | None = None,
                 token_getter: Callable[[], str] | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.transport = transport or FyersTransport()
        self._app_id_getter = app_id_getter or (lambda: os.environ.get("FYERS_APP_ID"))
        self._token_getter = token_getter or read_access_token
        self._clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def request_url(instrument: FyersInstrument, timeframe: Timeframe,
                    from_date: date, to_date: date) -> str:
        if timeframe not in RESOLUTIONS:
            raise FyersError("unsupported FYERS resolution")
        days = (to_date - from_date).days + 1
        limit = 366 if timeframe is Timeframe.DAY else 100
        if not 1 <= days <= limit:
            raise FyersError("FYERS request range exceeds the documented limit")
        if instrument.identity_verified and (
            instrument.valid_from is None or instrument.valid_to is None
            or from_date < instrument.valid_from or to_date > instrument.valid_to
        ):
            raise FyersError("instrument mapping does not cover requested history")
        query = urlencode({
            "symbol": instrument.ticker,
            "resolution": RESOLUTIONS[timeframe],
            "date_format": 1,
            "range_from": from_date.isoformat(),
            "range_to": to_date.isoformat(),
            "cont_flag": 0,
        })
        return f"{HISTORY_URL}?{query}"

    def fetch(self, instrument: FyersInstrument, timeframe: Timeframe,
              from_date: date, to_date: date) -> FyersBatch:
        url = self.request_url(instrument, timeframe, from_date, to_date)
        app_id = self._app_id_getter()
        if not app_id or not re.fullmatch(r"[A-Za-z0-9-]{1,64}", app_id):
            raise FyersError("FYERS App ID is unavailable or invalid")
        token = self._token_getter()
        if not token or any(char.isspace() for char in token):
            raise FyersError("FYERS token is unavailable")
        response = self.transport.get(
            url, {"Accept": "application/json",
                  "Authorization": f"{app_id}:{token}",
                  "User-Agent": FYERS_USER_AGENT}, 10.0, 2_000_000,
        )
        if response.status_code in (401, 403, 429):
            category = {
                401: "authentication rejected",
                403: ("access forbidden; possible API edge denial, invalid "
                      "authorization, application permissions, or another "
                      "provider restriction"),
                429: "rate limit reached; no retry",
            }[response.status_code]
            code = self._safe_code(response.body)
            detail = f"; code {code}: {_SAFE_CODES[code]}" if code is not None else ""
            raise FyersError(
                f"FYERS HTTP {response.status_code}: {category}{detail}",
                status_code=response.status_code, provider_code=code,
            )
        if response.status_code != 200:
            code = self._safe_code(response.body)
            detail = f"; code {code}: {_SAFE_CODES[code]}" if code is not None else ""
            raise FyersError(
                f"FYERS HTTP {response.status_code}: request failed{detail}",
                status_code=response.status_code, provider_code=code,
            )
        if len(response.body) > 2_000_000 or token.encode() in response.body:
            raise FyersError("FYERS response rejected")
        return self.parse_response(
            response.body, instrument, timeframe, from_date, to_date,
            retrieved_at_utc=self._clock(), source_ref=url,
        )

    @staticmethod
    def _safe_code(body: bytes) -> int | None:
        if len(body) > 2_000_000:
            return None
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError):
            return None
        code = payload.get("code") if isinstance(payload, dict) else None
        return code if type(code) is int and code in _SAFE_CODES else None

    def parse_response(self, body: bytes, instrument: FyersInstrument,
                       timeframe: Timeframe, from_date: date, to_date: date,
                       *, retrieved_at_utc: datetime,
                       source_ref: str = "offline-synthetic") -> FyersBatch:
        self.request_url(instrument, timeframe, from_date, to_date)
        if retrieved_at_utc.utcoffset() != timedelta(0):
            raise FyersError("retrieval timestamp must be UTC")
        if len(body) > 2_000_000:
            raise FyersError("FYERS response exceeds size limit")
        try:
            payload = json.loads(body.decode("utf-8"), parse_float=Decimal)
        except (UnicodeError, ValueError):
            raise FyersError("FYERS response is not valid JSON") from None
        if not isinstance(payload, dict) or payload.get("s") != "ok" or (
            payload.get("code", 200) != 200
        ):
            code = self._safe_code(body)
            detail = f"; code {code}: {_SAFE_CODES[code]}" if code is not None else ""
            raise FyersError(f"FYERS response status is not success{detail}",
                             provider_code=code)
        rows = payload.get("candles")
        if not isinstance(rows, list):
            raise FyersError("FYERS candle list is missing")
        candles: list[FyersCandle] = []
        previous: datetime | None = None
        for index, row in enumerate(rows, 1):
            if not isinstance(row, list) or len(row) != 6:
                raise FyersError(f"FYERS candle {index} has invalid field count")
            epoch = row[0]
            if type(epoch) is not int:
                raise FyersError(f"FYERS candle {index} has invalid timestamp")
            try:
                start = datetime.fromtimestamp(epoch, UTC)
            except (OverflowError, OSError, ValueError):
                raise FyersError(
                    f"FYERS candle {index} has invalid timestamp"
                ) from None
            if previous is not None and start <= previous:
                raise FyersError("FYERS candles are duplicate or out of order")
            previous = start
            local_day = start.astimezone(INDIA).date()
            if not from_date <= local_day <= to_date:
                raise FyersError(f"FYERS candle {index} is outside the requested range")
            prices: list[int] = []
            for label, value in zip(("open", "high", "low", "close"), row[1:5],
                                    strict=True):
                if type(value) not in (str, int, Decimal):
                    raise FyersError(f"FYERS candle {index} has invalid {label}")
                try:
                    prices.append(decimal_paise(str(value), label))
                except MarketDataError:
                    raise FyersError(
                        f"FYERS candle {index} has invalid {label}"
                    ) from None
            opened, high, low, closed = prices
            if not low <= opened <= high or not low <= closed <= high:
                raise FyersError(f"FYERS candle {index} has invalid OHLC range")
            volume = row[5]
            if type(volume) is not int or not 0 <= volume <= 2**63 - 1:
                raise FyersError(f"FYERS candle {index} has invalid volume")
            if timeframe is Timeframe.DAY:
                if any(bar.trade_date == local_day for bar in candles):
                    raise FyersError("FYERS daily candles have duplicate dates")
                complete = local_day < retrieved_at_utc.astimezone(INDIA).date()
            else:
                complete = (
                    start + timedelta(minutes=timeframe.minutes) <= retrieved_at_utc
                )
            candles.append(FyersCandle(
                start, local_day, opened, high, low, closed, volume, complete,
                f"{instrument.ticker}:{RESOLUTIONS[timeframe]}:{epoch}",
                dict(zip(
                    ("epoch", "open", "high", "low", "close", "volume"),
                    (str(value) for value in row), strict=True,
                )),
            ))
        return FyersBatch(
            instrument, timeframe, from_date, to_date, tuple(candles),
            retrieved_at_utc, hashlib.sha256(body).hexdigest(), source_ref, body,
        )
