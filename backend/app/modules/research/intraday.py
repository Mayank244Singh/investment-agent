"""Offline, point-in-time intraday research on explicitly supplied sessions.

No database, provider client, order path, or execution P&L is used here.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.core import AdjustmentStatus

INDIA = ZoneInfo("Asia/Kolkata")
INTRADAY_HORIZONS = (5, 15, 30, 60)  # elapsed minutes, not bar counts
INTRADAY_FEATURES = (
    "return_1m", "return_5m", "return_15m", "momentum",
    "rolling_volatility", "sma_paise", "ema_paise", "rsi_14",
    "macd_paise", "macd_signal_paise", "macd_histogram_paise",
    "atr_14_paise", "volume_spike", "relative_volume", "session_vwap_paise",
    "distance_from_vwap", "intraday_range", "opening_range_high_paise",
    "opening_range_low_paise", "opening_range_breakout",
    "minutes_from_open", "minutes_to_close",
)


def _utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timedelta(0)


@dataclass(frozen=True)
class ExchangeSession:
    day: date
    opens_local: time
    closes_local: time
    special: bool = False

    def __post_init__(self) -> None:
        if (
            self.opens_local.tzinfo is not None
            or self.closes_local.tzinfo is not None
            or self.opens_local >= self.closes_local
        ):
            raise ValueError("invalid exchange-local session boundary")

    @property
    def opens_utc(self) -> datetime:
        return datetime.combine(self.day, self.opens_local, INDIA).astimezone(UTC)

    @property
    def closes_utc(self) -> datetime:
        return datetime.combine(self.day, self.closes_local, INDIA).astimezone(UTC)


@dataclass(frozen=True)
class ExchangeCalendar:
    """Caller-verified sessions, including special sessions and explicit holidays."""

    sessions: tuple[ExchangeSession, ...]
    holidays: frozenset[date] = frozenset()

    def __post_init__(self) -> None:
        days = tuple(item.day for item in self.sessions)
        if not days or days != tuple(sorted(set(days))):
            raise ValueError("sessions must be nonempty, unique and ordered")
        if set(days) & self.holidays:
            raise ValueError("holiday cannot also be a trading session")

    def session_for(self, timestamp_utc: datetime) -> ExchangeSession | None:
        if not _utc(timestamp_utc):
            raise ValueError("timestamp must be explicit UTC")
        local_day = timestamp_utc.astimezone(INDIA).date()
        return next((item for item in self.sessions if item.day == local_day), None)


@dataclass(frozen=True)
class IntradayCandle:
    instrument_id: int
    timeframe: Timeframe
    start_utc: datetime
    end_utc: datetime
    available_at_utc: datetime | None
    is_complete: bool
    open_paise: int
    high_paise: int
    low_paise: int
    close_paise: int
    volume: int
    provider: str
    source_record_key: str
    adjustment_status: AdjustmentStatus
    identity_verified: bool
    corporate_action_flag: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.timeframe, Timeframe) or self.timeframe is Timeframe.DAY:
            raise ValueError("intraday candle needs a minute timeframe")
        if not _utc(self.start_utc) or not _utc(self.end_utc):
            raise ValueError("candle timestamps must be explicit UTC")
        if self.end_utc - self.start_utc != timedelta(minutes=self.timeframe.minutes):
            raise ValueError("candle duration does not match timeframe")
        if self.available_at_utc is not None and (
            not _utc(self.available_at_utc)
            or self.available_at_utc < self.end_utc
        ):
            raise ValueError("source availability must be UTC and after candle end")
        if (
            type(self.instrument_id) is not int or self.instrument_id <= 0
            or any(type(value) is not int or value <= 0 for value in (
                self.open_paise, self.high_paise, self.low_paise, self.close_paise
            ))
            or self.low_paise > min(self.open_paise, self.close_paise)
            or self.high_paise < max(self.open_paise, self.close_paise)
            or type(self.volume) is not int or self.volume < 0
            or type(self.is_complete) is not bool
            or type(self.identity_verified) is not bool
            or not self.provider or not self.source_record_key
            or self.adjustment_status not in {
                "verified_no_action", "verified_adjusted", "unverified"
            }
        ):
            raise ValueError("invalid intraday OHLCV or provenance")


@dataclass(frozen=True)
class IntradayConfig:
    dataset_version: str
    calendar: ExchangeCalendar
    timeframe: Timeframe
    built_at_utc: datetime
    strategy: StrategyFamily = StrategyFamily.INTRADAY
    horizons_minutes: tuple[int, ...] = INTRADAY_HORIZONS
    decision_lag_seconds: int = 0
    momentum_bars: int = 3
    rolling_window_bars: int = 5
    opening_range_minutes: int = 15
    required_features: tuple[str, ...] = ("session_vwap_paise",)
    allow_overnight: bool = False

    def __post_init__(self) -> None:
        if (
            not self.dataset_version or self.strategy is not StrategyFamily.INTRADAY
            or not isinstance(self.timeframe, Timeframe)
            or self.timeframe is Timeframe.DAY or not _utc(self.built_at_utc)
            or type(self.decision_lag_seconds) is not int
            or self.decision_lag_seconds < 0
            or self.momentum_bars < 1 or self.rolling_window_bars < 2
            or self.opening_range_minutes < 1
            or not self.horizons_minutes
            or any(type(value) is not int or value <= 0
                   for value in self.horizons_minutes)
            or tuple(sorted(set(self.horizons_minutes))) != self.horizons_minutes
            or any(name not in INTRADAY_FEATURES for name in self.required_features)
        ):
            raise ValueError("invalid intraday research configuration")
        if self.allow_overnight:
            raise ValueError("overnight targets need a separately reviewed policy")


@dataclass(frozen=True)
class IntradaySample:
    instrument_id: int
    session: date
    timeframe: Timeframe
    decision_at_utc: datetime
    target_horizon_minutes: int
    target_end_utc: datetime | None
    target_available_at_utc: datetime | None
    features: dict[str, Decimal | None]
    missing_features: tuple[str, ...]
    target_return: Decimal | None
    positive_return_label: int | None
    provenance: tuple[str, ...]
    availability_times_utc: tuple[datetime | None, ...]
    adjustment_status: AdjustmentStatus
    dataset_version: str
    rejection_reasons: tuple[str, ...]

    @property
    def usable(self) -> bool:
        return not self.rejection_reasons


@dataclass(frozen=True)
class IntradayReport:
    instruments: int
    input_bars: int
    candidates: int
    accepted: int
    rejected: int
    duplicate_timestamps: int
    out_of_order_observations: int
    missing_expected_bars: int
    missing_features: dict[str, int]
    missing_targets: int
    rejection_reasons: dict[str, int]


@dataclass(frozen=True)
class IntradayDataset:
    samples: tuple[IntradaySample, ...]
    report: IntradayReport


def _ratio(numerator: int | Decimal, denominator: int | Decimal) -> Decimal:
    return Decimal(numerator) / Decimal(denominator)


def _ema(values: list[Decimal], period: int) -> Decimal | None:
    if len(values) < period:
        return None
    value = sum(values[:period]) / Decimal(period)
    alpha = Decimal(2) / Decimal(period + 1)
    for item in values[period:]:
        value += alpha * (item - value)
    return value


def _macd(values: list[Decimal]) -> tuple[Decimal | None, Decimal | None]:
    if len(values) < 26:
        return None, None
    macd_values: list[Decimal] = []
    for end in range(26, len(values) + 1):
        fast = _ema(values[:end], 12)
        slow = _ema(values[:end], 26)
        assert fast is not None and slow is not None
        macd_values.append(fast - slow)
    return macd_values[-1], _ema(macd_values, 9)


def _rsi(closes: list[int]) -> Decimal | None:
    if len(closes) < 15:
        return None
    changes = [closes[index] - closes[index - 1]
               for index in range(1, len(closes))]
    gains = [Decimal(max(change, 0)) for change in changes]
    losses = [Decimal(max(-change, 0)) for change in changes]
    average_gain = sum(gains[:14], Decimal(0)) / Decimal(14)
    average_loss = sum(losses[:14], Decimal(0)) / Decimal(14)
    for gain, loss in zip(gains[14:], losses[14:], strict=True):
        average_gain = (average_gain * 13 + gain) / 14
        average_loss = (average_loss * 13 + loss) / 14
    if average_loss == 0:
        return Decimal(100) if average_gain > 0 else Decimal(50)
    return Decimal(100) - Decimal(100) / (1 + average_gain / average_loss)


def _atr(bars: list[IntradayCandle]) -> Decimal | None:
    if len(bars) < 14:
        return None
    true_ranges: list[Decimal] = []
    for index, bar in enumerate(bars):
        previous_close = bars[index - 1].close_paise if index else bar.close_paise
        true_ranges.append(Decimal(max(
            bar.high_paise - bar.low_paise,
            abs(bar.high_paise - previous_close),
            abs(bar.low_paise - previous_close),
        )))
    value = sum(true_ranges[:14], Decimal(0)) / Decimal(14)
    for item in true_ranges[14:]:
        value = (value * 13 + item) / 14
    return value


def _features(
    history: list[IntradayCandle], session: ExchangeSession,
    config: IntradayConfig,
) -> dict[str, Decimal | None]:
    """Compute only on the completed, available prefix of one session."""
    result: dict[str, Decimal | None] = dict.fromkeys(INTRADAY_FEATURES)
    current = history[-1]
    closes = [item.close_paise for item in history]
    close_values = [Decimal(value) for value in closes]
    by_end = {item.end_utc: item for item in history}
    for minutes in (1, 5, 15):
        prior = by_end.get(current.end_utc - timedelta(minutes=minutes))
        if prior is not None:
            result[f"return_{minutes}m"] = _ratio(
                current.close_paise, prior.close_paise
            ) - 1
    if len(history) > config.momentum_bars:
        result["momentum"] = _ratio(
            closes[-1], closes[-config.momentum_bars - 1]
        ) - 1
    window = config.rolling_window_bars
    if len(history) >= window:
        result["sma_paise"] = sum(closes[-window:]) / Decimal(window)
    result["ema_paise"] = _ema(close_values, window)
    if len(history) > window:
        returns = [
            _ratio(closes[index], closes[index - 1]) - 1
            for index in range(len(closes) - window, len(closes))
        ]
        mean = sum(returns) / Decimal(window)
        result["rolling_volatility"] = (
            sum((value - mean) ** 2 for value in returns) / Decimal(window)
        ).sqrt()
        previous_volume_mean = sum(
            item.volume for item in history[-window - 1:-1]
        ) / Decimal(window)
        if previous_volume_mean > 0:
            relative = Decimal(current.volume) / previous_volume_mean
            result["relative_volume"] = relative
            result["volume_spike"] = Decimal(int(relative >= 2))
    result["rsi_14"] = _rsi(closes)
    macd, signal = _macd(close_values)
    result["macd_paise"] = macd
    result["macd_signal_paise"] = signal
    if macd is not None and signal is not None:
        result["macd_histogram_paise"] = macd - signal
    result["atr_14_paise"] = _atr(history)
    total_volume = sum(item.volume for item in history)
    if total_volume:
        typical_volume = sum((
            Decimal(item.high_paise + item.low_paise + item.close_paise)
            * item.volume / 3 for item in history
        ), Decimal(0))
        vwap = typical_volume / total_volume
        result["session_vwap_paise"] = vwap
        result["distance_from_vwap"] = _ratio(current.close_paise, vwap) - 1
    running_high = max(item.high_paise for item in history)
    running_low = min(item.low_paise for item in history)
    result["intraday_range"] = _ratio(
        running_high - running_low, history[0].open_paise
    )
    opening_end = session.opens_utc + timedelta(
        minutes=config.opening_range_minutes
    )
    if current.end_utc >= opening_end:
        opening_bars = [item for item in history if item.end_utc <= opening_end]
        if opening_bars:
            opening_high = max(item.high_paise for item in opening_bars)
            opening_low = min(item.low_paise for item in opening_bars)
            result["opening_range_high_paise"] = Decimal(opening_high)
            result["opening_range_low_paise"] = Decimal(opening_low)
            result["opening_range_breakout"] = Decimal(
                int(current.close_paise > opening_high)
                - int(current.close_paise < opening_low)
            )
    result["minutes_from_open"] = Decimal(
        (current.end_utc - session.opens_utc).total_seconds()
    ) / 60
    result["minutes_to_close"] = Decimal(
        (session.closes_utc - current.end_utc).total_seconds()
    ) / 60
    return result


def _quality_reasons(
    candle: IntradayCandle, *, at: datetime,
) -> set[str]:
    reasons: set[str] = set()
    if not candle.is_complete:
        reasons.add("incomplete candle")
    if candle.available_at_utc is None:
        reasons.add("historical availability unknown")
    elif candle.available_at_utc > at:
        reasons.add("candle unavailable at required time")
    if not candle.identity_verified:
        reasons.add("historical identity unverified")
    if candle.adjustment_status == "unverified":
        reasons.add("adjustment status unverified")
    if candle.corporate_action_flag:
        reasons.add("corporate action unresolved")
    return reasons


def build_intraday_dataset(
    candles: tuple[IntradayCandle, ...], config: IntradayConfig,
) -> IntradayDataset:
    """Build candidate labels without using future or unavailable bars as features."""
    groups: dict[tuple[int, date], list[IntradayCandle]] = defaultdict(list)
    for candle in candles:
        if candle.timeframe is not config.timeframe:
            raise ValueError("mixed or mismatched intraday timeframe")
        key = candle.instrument_id, candle.start_utc.astimezone(INDIA).date()
        groups[key].append(candle)
    duplicate_count = 0
    disorder_count = 0
    missing_count = 0
    samples: list[IntradaySample] = []
    reasons_count: Counter[str] = Counter()
    missing_features: Counter[str] = Counter()
    for (instrument_id, day), supplied in sorted(groups.items()):
        session = config.calendar.session_for(supplied[0].start_utc)
        group_reasons: set[str] = set()
        if day in config.calendar.holidays:
            group_reasons.add("market holiday")
        if session is None:
            group_reasons.add("outside configured exchange session")
        if any(
            supplied[index].start_utc < supplied[index - 1].start_utc
            for index in range(1, len(supplied))
        ):
            disorder_count += 1
            group_reasons.add("out-of-order observations")
        by_start: dict[datetime, IntradayCandle] = {}
        for candle in supplied:
            if candle.start_utc in by_start:
                duplicate_count += 1
                group_reasons.add("duplicate timestamps")
            else:
                by_start[candle.start_utc] = candle
            if session is not None and (
                candle.start_utc < session.opens_utc
                or candle.end_utc > session.closes_utc
                or (candle.start_utc - session.opens_utc)
                % timedelta(minutes=config.timeframe.minutes)
                != timedelta(0)
            ):
                group_reasons.add("candle outside session grid")
        if session is not None and by_start:
            last_end = min(max(item.end_utc for item in supplied), session.closes_utc)
            tick = session.opens_utc
            while tick < last_end:
                if tick not in by_start:
                    missing_count += 1
                tick += timedelta(minutes=config.timeframe.minutes)
        for current in sorted(supplied, key=lambda item: item.start_utc):
            decision = current.end_utc + timedelta(
                seconds=config.decision_lag_seconds
            )
            common = set(group_reasons)
            if decision > config.built_at_utc:
                common.add("decision after dataset build")
            history: list[IntradayCandle] = []
            if session is not None and not group_reasons:
                tick = session.opens_utc
                while tick < current.end_utc:
                    prior = by_start.get(tick)
                    if prior is None:
                        common.add("missing minute bars")
                    else:
                        history.append(prior)
                    tick += timedelta(minutes=config.timeframe.minutes)
                for prior in history:
                    common.update(_quality_reasons(prior, at=decision))
            if not history:
                common.add("missing feature history")
            features = (
                _features(history, session, config)
                if session is not None and history and not common
                else dict.fromkeys(INTRADAY_FEATURES)
            )
            missing = tuple(
                name for name, value in features.items() if value is None
            )
            missing_features.update(missing)
            if set(missing) & set(config.required_features):
                common.add("missing required feature")
            for horizon in config.horizons_minutes:
                reasons = set(common)
                end = current.end_utc + timedelta(minutes=horizon)
                target_end: datetime | None = end
                target_available: datetime | None = None
                target_return: Decimal | None = None
                positive: int | None = None
                if horizon % config.timeframe.minutes:
                    reasons.add("horizon incompatible with timeframe")
                    target_end = None
                elif session is None or end > session.closes_utc:
                    reasons.add("target crosses exchange session close")
                    target_end = None
                else:
                    expected = current.end_utc
                    future: list[IntradayCandle] = []
                    while expected < end:
                        next_bar = by_start.get(expected)
                        if next_bar is None:
                            reasons.add("missing target minute bars")
                        else:
                            future.append(next_bar)
                        expected += timedelta(minutes=config.timeframe.minutes)
                    for item in future:
                        reasons.update(_quality_reasons(
                            item, at=config.built_at_utc
                        ))
                    if future:
                        target_available = future[-1].available_at_utc
                    if not reasons and future:
                        target_return = _ratio(
                            future[-1].close_paise, current.close_paise
                        ) - 1
                        positive = int(target_return > 0)
                ordered = tuple(sorted(reasons))
                reasons_count.update(ordered)
                samples.append(IntradaySample(
                    instrument_id=instrument_id, session=day,
                    timeframe=config.timeframe, decision_at_utc=decision,
                    target_horizon_minutes=horizon, target_end_utc=target_end,
                    target_available_at_utc=target_available,
                    features=features.copy(), missing_features=missing,
                    target_return=target_return,
                    positive_return_label=positive,
                    provenance=tuple(
                        f"{item.provider}:{item.source_record_key}" for item in history
                    ),
                    availability_times_utc=tuple(
                        item.available_at_utc for item in history
                    ),
                    adjustment_status=current.adjustment_status,
                    dataset_version=config.dataset_version,
                    rejection_reasons=ordered,
                ))
    report = IntradayReport(
        instruments=len({item.instrument_id for item in candles}),
        input_bars=len(candles), candidates=len(samples),
        accepted=sum(item.usable for item in samples),
        rejected=sum(not item.usable for item in samples),
        duplicate_timestamps=duplicate_count,
        out_of_order_observations=disorder_count,
        missing_expected_bars=missing_count,
        missing_features=dict(sorted(missing_features.items())),
        missing_targets=sum(item.target_return is None for item in samples),
        rejection_reasons=dict(sorted(reasons_count.items())),
    )
    return IntradayDataset(tuple(samples), report)


@dataclass(frozen=True)
class IntradaySplit:
    train: tuple[IntradaySample, ...]
    validation: tuple[IntradaySample, ...]
    test: tuple[IntradaySample, ...]
    excluded: tuple[tuple[IntradaySample, str], ...]


def chronological_intraday_split(
    samples: tuple[IntradaySample, ...],
    train_end_utc: datetime,
    validation_end_utc: datetime,
    *,
    embargo: timedelta = timedelta(0),
) -> IntradaySplit:
    """Purge labels overlapping the next partition; never random-split bars."""
    if (
        not _utc(train_end_utc) or not _utc(validation_end_utc)
        or train_end_utc >= validation_end_utc or embargo < timedelta(0)
    ):
        raise ValueError("invalid intraday split boundaries")
    train: list[IntradaySample] = []
    validation: list[IntradaySample] = []
    test: list[IntradaySample] = []
    excluded: list[tuple[IntradaySample, str]] = []
    for sample in samples:
        at = sample.decision_at_utc
        label_end = sample.target_end_utc
        if not sample.usable or label_end is None:
            excluded.append((sample, "unusable sample"))
        elif at <= train_end_utc:
            if label_end > train_end_utc:
                excluded.append((sample, "training label overlaps validation"))
            else:
                train.append(sample)
        elif at <= train_end_utc + embargo:
            excluded.append((sample, "train/validation embargo"))
        elif at <= validation_end_utc:
            if label_end > validation_end_utc:
                excluded.append((sample, "validation label overlaps test"))
            else:
                validation.append(sample)
        elif at <= validation_end_utc + embargo:
            excluded.append((sample, "validation/test embargo"))
        else:
            test.append(sample)
    return IntradaySplit(
        tuple(train), tuple(validation), tuple(test), tuple(excluded)
    )
