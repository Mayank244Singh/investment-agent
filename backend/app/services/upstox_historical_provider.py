"""Isolated Upstox Historical Candle V3 adapter with injectable transport."""

import hashlib
import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from app.services.market_data_service import MarketDataError, decimal_paise

INDIA_TIMEZONE = ZoneInfo("Asia/Kolkata")
PROVIDER = "upstox"
# Codes published on Upstox's Historical Candle V3 documentation page.
# Unknown codes and all server-supplied messages stay out of diagnostics.
DOCUMENTED_HISTORICAL_ERROR_CODES = frozenset({
    "UDAPI1021", "UDAPI1022", "UDAPI100011", "UDAPI1015",
    "UDAPI1146", "UDAPI1147", "UDAPI1148",
})
# Fixed descriptions from Upstox's common error-code reference. Never use
# response messages, even when an error code is recognized.
DOCUMENTED_COMMON_ERROR_EXPLANATIONS = {
    "UDAPI10000": "request URL was not recognized",
    "UDAPI100016": "credentials were rejected",
    "UDAPI100050": "token was rejected",
    "UDAPI100067": "endpoint is not permitted for an extended token",
    "UDAPI100073": "client ID is inactive",
}


class UpstoxError(ValueError):
    """A safe-to-display error that never includes headers or response bodies."""


class UpstoxAuthenticationError(UpstoxError):
    """An Analytics Token is absent or the server denied a request."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        documented_error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.documented_error_code = documented_error_code


class UpstoxRateLimitError(UpstoxError):
    """A bounded request exhausted rate-limit retries."""


class UpstoxTransportError(UpstoxError):
    """The remote request could not complete after bounded retries."""


class UpstoxResponseError(UpstoxError):
    """The provider returned an unexpected or invalid response."""


@dataclass(frozen=True)
class UpstoxProviderConfig:
    """Explicit bounds keep a pilot request small and predictable."""

    base_url: str = "https://api.upstox.com"
    timeout_seconds: float = 10.0
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.5
    max_response_bytes: int = 2_000_000
    max_calendar_days: int = 31

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("Upstox base URL must be an HTTPS origin")
        if (
            not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 120
        ):
            raise ValueError("Upstox timeout must be between 0 and 120 seconds")
        if not 1 <= self.max_attempts <= 5:
            raise ValueError("Upstox max attempts must be between 1 and 5")
        if (
            not math.isfinite(self.initial_backoff_seconds)
            or not 0 <= self.initial_backoff_seconds <= 30
        ):
            raise ValueError("Upstox backoff must be between 0 and 30 seconds")
        if not 1 <= self.max_response_bytes <= 10_000_000:
            raise ValueError("Upstox response limit must be 1 to 10 MB")
        if not 1 <= self.max_calendar_days <= 366:
            raise ValueError("Upstox range limit must be 1 to 366 days")


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]


class HistoricalTransport(Protocol):
    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> HttpResponse: ...


class UrllibTransport:
    """Small synchronous transport; never logs request headers or bodies."""

    class _NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, request: Request, fp: object, code: int,
                             msg: str, headers: object, newurl: str) -> None:
            return None

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> HttpResponse:
        request = Request(url, headers=dict(headers), method="GET")
        try:
            with build_opener(self._NoRedirect()).open(
                request, timeout=timeout_seconds
            ) as response:
                body = response.read(max_response_bytes + 1)
                return HttpResponse(response.status, body, dict(response.headers))
        except HTTPError as response:
            return HttpResponse(
                response.code,
                response.read(max_response_bytes + 1),
                dict(response.headers),
            )
        except (URLError, TimeoutError, OSError):
            raise UpstoxTransportError("Upstox request failed") from None


@dataclass(frozen=True)
class UpstoxCandle:
    trade_date: date
    timestamp_at_utc: datetime
    open_price: str
    high_price: str
    low_price: str
    close_price: str
    volume: int
    open_interest: int | None
    raw_payload: Mapping[str, str]


@dataclass(frozen=True)
class HistoricalBatch:
    instrument_key: str
    from_date: date
    to_date: date
    candles: tuple[UpstoxCandle, ...]
    response_sha256: str
    retrieved_at_utc: datetime
    request_id: str
    source_ref: str


class UpstoxHistoricalDataProvider:
    """Retrieve and parse one bounded daily NSE EQ request."""

    def __init__(
        self,
        config: UpstoxProviderConfig | None = None,
        *,
        transport: HistoricalTransport | None = None,
        token_getter: Callable[[], str | None] | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.config = config or UpstoxProviderConfig()
        self.transport = transport or UrllibTransport()
        self._token_getter = token_getter or (
            lambda: os.environ.get("UPSTOX_ANALYTICS_TOKEN")
        )
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep or time.sleep
        self._request_id_factory = request_id_factory or (lambda: uuid.uuid4().hex)

    def fetch_daily(
        self, instrument_key: str, from_date: date, to_date: date
    ) -> HistoricalBatch:
        """Use the Analytics Token only in the request header at call time."""
        url = self._url(instrument_key, from_date, to_date)
        token = self._token_getter()
        if not token or not token.strip():
            raise UpstoxAuthenticationError("Upstox Analytics Token is unavailable")
        request_id = self._request_id_factory()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Request-ID": request_id,
        }
        for attempt in range(self.config.max_attempts):
            try:
                result = self.transport.get(
                    url,
                    headers,
                    self.config.timeout_seconds,
                    self.config.max_response_bytes,
                )
            except (UpstoxTransportError, TimeoutError, OSError):
                if attempt + 1 == self.config.max_attempts:
                    raise UpstoxTransportError("Upstox request failed") from None
                self._sleep(self.config.initial_backoff_seconds * (2**attempt))
                continue
            if result.status_code in (401, 403):
                category = (
                    "authentication rejected"
                    if result.status_code == 401
                    else "access forbidden"
                )
                documented_code = (
                    self._documented_error_code(result.body)
                    if len(result.body) <= self.config.max_response_bytes
                    else None
                )
                if documented_code in DOCUMENTED_COMMON_ERROR_EXPLANATIONS:
                    explanation = DOCUMENTED_COMMON_ERROR_EXPLANATIONS[documented_code]
                    detail = f"; documented error code {documented_code}: {explanation}"
                elif documented_code is not None:
                    detail = (
                        f"; documented error code {documented_code}: "
                        "historical request was rejected"
                    )
                else:
                    detail = "; documented error code unavailable"
                raise UpstoxAuthenticationError(
                    f"Upstox HTTP {result.status_code}: {category}{detail}",
                    status_code=result.status_code,
                    documented_error_code=documented_code,
                )
            if len(result.body) > self.config.max_response_bytes:
                raise UpstoxResponseError("Upstox response exceeds size limit")
            if token.encode("utf-8") in result.body:
                raise UpstoxResponseError("Upstox response contained a credential")
            if result.status_code == 429 or 500 <= result.status_code <= 599:
                if attempt + 1 == self.config.max_attempts:
                    if result.status_code == 429:
                        raise UpstoxRateLimitError("Upstox rate limit persisted")
                    raise UpstoxTransportError("Upstox server error persisted")
                self._sleep(self._retry_delay(result.headers, attempt))
                continue
            if result.status_code != 200:
                raise UpstoxResponseError(
                    "Upstox rejected the historical request "
                    f"(HTTP {result.status_code})"
                )
            return self.parse_response(
                result.body,
                instrument_key,
                from_date,
                to_date,
                retrieved_at_utc=self._clock(),
                request_id=request_id,
                source_ref=url,
            )
        raise UpstoxTransportError("Upstox request failed")

    def parse_response(
        self,
        body: bytes,
        instrument_key: str,
        from_date: date,
        to_date: date,
        *,
        retrieved_at_utc: datetime,
        request_id: str = "offline-mock",
        source_ref: str = "offline-mock",
    ) -> HistoricalBatch:
        """Parse daily candles without sending a request or using a token."""
        self._validate_request(instrument_key, from_date, to_date)
        if retrieved_at_utc.utcoffset() is None:
            raise UpstoxResponseError("retrieval time must include a timezone")
        if len(body) > self.config.max_response_bytes:
            raise UpstoxResponseError("Upstox response exceeds size limit")
        try:
            payload = json.loads(body.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise UpstoxResponseError("Upstox response is not valid JSON") from None
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise UpstoxResponseError("Upstox response status is not success")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("candles"), list):
            raise UpstoxResponseError("Upstox candle list is missing")
        candles: list[UpstoxCandle] = []
        dates: set[date] = set()
        for index, raw in enumerate(data["candles"], 1):
            candle = self._parse_candle(raw, from_date, to_date, index)
            if candle.trade_date in dates:
                raise UpstoxResponseError(
                    f"duplicate Upstox daily candle on {candle.trade_date}"
                )
            dates.add(candle.trade_date)
            candles.append(candle)
        return HistoricalBatch(
            instrument_key=instrument_key,
            from_date=from_date,
            to_date=to_date,
            candles=tuple(sorted(candles, key=lambda item: item.trade_date)),
            response_sha256=hashlib.sha256(body).hexdigest(),
            retrieved_at_utc=retrieved_at_utc.astimezone(UTC),
            request_id=request_id,
            source_ref=source_ref,
        )

    def _url(self, instrument_key: str, from_date: date, to_date: date) -> str:
        self._validate_request(instrument_key, from_date, to_date)
        return (
            f"{self.config.base_url.rstrip('/')}/v3/historical-candle/"
            f"{quote(instrument_key, safe='')}/days/1/"
            f"{to_date.isoformat()}/{from_date.isoformat()}"
        )

    def _validate_request(
        self, instrument_key: str, from_date: date, to_date: date
    ) -> None:
        if (
            not instrument_key.startswith("NSE_EQ|")
            or len(instrument_key) <= len("NSE_EQ|")
            or len(instrument_key) > 255
            or any(char.isspace() or char in "/\\?#" for char in instrument_key)
        ):
            raise UpstoxResponseError("a valid NSE EQ instrument key is required")
        days = (to_date - from_date).days + 1
        if days < 1 or days > self.config.max_calendar_days:
            raise UpstoxResponseError("historical date range is invalid or too large")

    def _parse_candle(
        self, raw: object, from_date: date, to_date: date, index: int
    ) -> UpstoxCandle:
        if not isinstance(raw, list) or len(raw) not in (6, 7):
            raise UpstoxResponseError(f"candle {index} must have 6 or 7 fields")
        timestamp = raw[0]
        if not isinstance(timestamp, str):
            raise UpstoxResponseError(f"candle {index} timestamp is missing")
        try:
            instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            raise UpstoxResponseError(
                f"candle {index} timestamp is malformed"
            ) from None
        if instant.utcoffset() is None:
            raise UpstoxResponseError(f"candle {index} timestamp lacks timezone")
        trade_date = instant.astimezone(INDIA_TIMEZONE).date()
        if not from_date <= trade_date <= to_date:
            raise UpstoxResponseError(f"candle {index} is outside the requested range")
        values: list[str] = []
        paise: list[int] = []
        for label, value in zip(
            ("open", "high", "low", "close"), raw[1:5], strict=True
        ):
            if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
                raise UpstoxResponseError(f"candle {index} {label} is invalid")
            value_text = str(value)
            try:
                paise.append(decimal_paise(value_text, label))
            except MarketDataError:
                raise UpstoxResponseError(
                    f"candle {index} {label} is invalid"
                ) from None
            values.append(value_text)
        opened, high, low, closed = paise
        if low > high or not low <= opened <= high or not low <= closed <= high:
            raise UpstoxResponseError(f"candle {index} OHLC range is invalid")
        volume = raw[5]
        if type(volume) is not int or not 0 <= volume <= 2**63 - 1:
            raise UpstoxResponseError(f"candle {index} volume is invalid")
        open_interest = raw[6] if len(raw) == 7 else None
        if open_interest is not None and (
            type(open_interest) is not int or not 0 <= open_interest <= 2**63 - 1
        ):
            raise UpstoxResponseError(f"candle {index} open interest is invalid")
        raw_payload = {
            "timestamp": timestamp,
            "open": values[0],
            "high": values[1],
            "low": values[2],
            "close": values[3],
            "volume": str(volume),
        }
        if open_interest is not None:
            raw_payload["open_interest"] = str(open_interest)
        return UpstoxCandle(
            trade_date=trade_date,
            timestamp_at_utc=instant.astimezone(UTC),
            open_price=values[0],
            high_price=values[1],
            low_price=values[2],
            close_price=values[3],
            volume=volume,
            open_interest=open_interest,
            raw_payload=raw_payload,
        )

    def _retry_delay(self, headers: Mapping[str, str], attempt: int) -> float:
        for name, value in headers.items():
            if name.lower() == "retry-after":
                try:
                    delay: float = float(value)
                    if math.isfinite(delay):
                        return min(60.0, max(0.0, delay))
                except ValueError:
                    break
        factor: float = float(2**attempt)
        return self.config.initial_backoff_seconds * factor

    @staticmethod
    def _documented_error_code(body: bytes) -> str | None:
        """Allowlist official V3 and common codes; ignore response messages."""
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        errors = payload.get("errors")
        if not isinstance(errors, list):
            return None
        for error in errors:
            if not isinstance(error, dict):
                continue
            for field in ("errorCode", "error_code"):
                code = error.get(field)
                if isinstance(code, str) and (
                    code in DOCUMENTED_HISTORICAL_ERROR_CODES
                    or code in DOCUMENTED_COMMON_ERROR_EXPLANATIONS
                ):
                    return code
        return None
