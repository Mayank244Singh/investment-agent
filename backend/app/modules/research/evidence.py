"""Read-only, point-in-time evidence for Indian equity research.

These contracts cannot submit orders or approve the production risk gate.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.intraday import ExchangeCalendar, IntradayCandle


class Exchange(StrEnum):
    NSE = "NSE"
    BSE = "BSE"


class EvidenceKind(StrEnum):
    PREDICTION = "prediction"
    TECHNICAL = "technical"
    FUNDAMENTAL = "fundamental"
    MARKET_REGIME = "market_regime"
    PORTFOLIO_CONTEXT = "portfolio_context"
    DATA_QUALITY = "data_quality"


class Stance(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class Quality(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    INVALID = "invalid"


class AssessmentState(StrEnum):
    ACCEPT = "ACCEPT"
    ACCEPT_WITH_REDUCED_SIZE = "ACCEPT_WITH_REDUCED_SIZE"
    WAIT = "WAIT"
    REJECT = "REJECT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


def _is_utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timedelta(0)


def _valid_horizon(timeframe: Timeframe, horizon: int) -> bool:
    """Match Phase 7B's minute or trading-session horizons, without guessing units."""
    if type(horizon) is not int:
        return False
    if timeframe is Timeframe.DAY:
        return horizon in (5, 10, 60, 120)  # trading sessions
    return horizon in (5, 15, 30, 60) and horizon % timeframe.minutes == 0


@dataclass(frozen=True)
class IndianInstrument:
    instrument_id: int
    exchange: Exchange
    symbol: str
    isin: str | None
    identity_verified: bool

    def __post_init__(self) -> None:
        if (
            type(self.instrument_id) is not int or self.instrument_id <= 0
            or not isinstance(self.exchange, Exchange)
            or not self.symbol or self.symbol != self.symbol.strip().upper()
            or (self.isin is not None and not self.isin.strip())
            or type(self.identity_verified) is not bool
        ):
            raise ValueError("invalid Indian instrument identity")


@dataclass(frozen=True)
class Evidence:
    instrument: IndianInstrument
    kind: EvidenceKind
    stance: Stance
    provider: str
    source_record_key: str
    observed_at_utc: datetime
    available_at_utc: datetime | None
    decision_cutoff_utc: datetime
    quality: Quality
    method_version: str
    input_lineage: tuple[str, ...]
    usage_verified: bool
    timeframe: Timeframe
    horizon: int | None = None
    value: Decimal | None = None
    calibrated_uncertainty: Decimal | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, EvidenceKind)
            or not isinstance(self.stance, Stance)
            or not isinstance(self.quality, Quality)
            or not isinstance(self.timeframe, Timeframe)
            or not all(_is_utc(item) for item in (
                self.observed_at_utc, self.decision_cutoff_utc
            ))
            or (self.available_at_utc is not None
                and not _is_utc(self.available_at_utc))
            or (self.available_at_utc is not None
                and self.available_at_utc < self.observed_at_utc)
            or not self.provider or not self.source_record_key
            or not self.method_version or not self.input_lineage
            or not all(self.input_lineage)
            or type(self.usage_verified) is not bool
            or (self.kind is EvidenceKind.PREDICTION and self.horizon is None)
            or (self.horizon is not None
                and not _valid_horizon(self.timeframe, self.horizon))
            or (self.value is not None and not self.value.is_finite())
            or (self.calibrated_uncertainty is not None and (
                not self.calibrated_uncertainty.is_finite()
                or self.calibrated_uncertainty < 0
            ))
        ):
            raise ValueError("invalid evidence contract")


@dataclass(frozen=True)
class CandleAssessment:
    instrument: IndianInstrument
    timeframe: Timeframe
    decision_at_utc: datetime
    usable: tuple[IntradayCandle, ...]
    issues: tuple[str, ...]
    deferred: tuple[str, ...]


def validate_completed_candles(
    bars: tuple[IntradayCandle, ...], *, instrument: IndianInstrument,
    calendar: ExchangeCalendar, calendar_exchange: Exchange,
    timeframe: Timeframe, window_start_utc: datetime,
    decision_at_utc: datetime, max_age: timedelta,
) -> CandleAssessment:
    """Validate only the completed prefix of one verified exchange session.

    Future or not-yet-available bars are never inspected as evidence.
    The caller supplies an authoritative calendar; this code invents no holidays.
    """
    if (
        not _is_utc(window_start_utc) or not _is_utc(decision_at_utc)
        or window_start_utc >= decision_at_utc or max_age <= timedelta(0)
    ):
        raise ValueError("invalid evidence decision window")
    def report(
        usable: tuple[IntradayCandle, ...], issues: tuple[str, ...],
        deferred: tuple[str, ...] = (),
    ) -> CandleAssessment:
        return CandleAssessment(instrument, timeframe, decision_at_utc,
                                usable, issues, deferred)

    if timeframe not in {
        Timeframe.MINUTE_1, Timeframe.MINUTE_5, Timeframe.MINUTE_15
    }:
        return report((), ("unsupported candle timeframe",))
    if calendar_exchange is not instrument.exchange:
        return report((), ("exchange/calendar mismatch",))
    session = calendar.session_for(decision_at_utc)
    if session is None:
        return report((), ("no verified exchange session or holiday",))
    interval = timedelta(minutes=timeframe.minutes)
    if (
        window_start_utc < session.opens_utc
        or decision_at_utc > session.closes_utc
        or (window_start_utc - session.opens_utc) % interval
    ):
        return report((), ("window outside or misaligned with session",))
    completed_end = session.opens_utc + (
        (decision_at_utc - session.opens_utc) // interval
    ) * interval
    if completed_end <= window_start_utc:
        return report((), ("no completed candle by decision",))
    issues: set[str] = set()
    deferred: set[str] = set()
    seen: set[datetime] = set()
    usable: list[IntradayCandle] = []
    for bar in bars:
        # Never inspect OHLCV or stance from a future bar.
        if bar.end_utc > decision_at_utc or bar.start_utc < window_start_utc:
            continue
        if bar.instrument_id != instrument.instrument_id:
            issues.add("instrument identity mismatch")
            continue
        if bar.timeframe is not timeframe:
            issues.add("candle timeframe mismatch")
            continue
        if bar.start_utc in seen:
            issues.add("duplicate candle")
            continue
        seen.add(bar.start_utc)
        if bar.start_utc < session.opens_utc or bar.end_utc > session.closes_utc:
            issues.add("candle outside exchange session")
            continue
        if (bar.start_utc - session.opens_utc) % interval:
            issues.add("candle outside session grid")
            continue
        if not bar.is_complete:
            issues.add("incomplete candle")
            continue
        if bar.available_at_utc is None:
            deferred.add("candle availability unknown")
            continue
        if bar.available_at_utc > decision_at_utc:
            deferred.add("candle arrived after decision")
            continue
        if not bar.identity_verified:
            issues.add("candle identity unverified")
            continue
        if bar.adjustment_status == "unverified" or bar.corporate_action_flag:
            issues.add("candle corporate action or adjustment unverified")
            continue
        usable.append(bar)
    usable.sort(key=lambda item: item.start_utc)
    expected = {
        window_start_utc + index * interval
        for index in range((completed_end - window_start_utc) // interval)
    }
    if expected - {bar.start_utc for bar in usable}:
        issues.add("missing completed candle interval")
    if usable and decision_at_utc - usable[-1].end_utc > max_age:
        issues.add("stale completed candle")
    return report(tuple(usable), tuple(sorted(issues)), tuple(sorted(deferred)))


@dataclass(frozen=True)
class OpportunityRequest:
    instrument: IndianInstrument
    strategy: StrategyFamily
    timeframe: Timeframe
    horizon: int
    decision_at_utc: datetime

    def __post_init__(self) -> None:
        if (
            not isinstance(self.strategy, StrategyFamily)
            or not isinstance(self.timeframe, Timeframe)
            or not _is_utc(self.decision_at_utc)
            or not _valid_horizon(self.timeframe, self.horizon)
            or (self.strategy is StrategyFamily.INTRADAY
                and self.timeframe is Timeframe.DAY)
            or (self.strategy is StrategyFamily.SWING
                and (self.timeframe is not Timeframe.DAY
                     or self.horizon not in (5, 10)))
            or (self.strategy is StrategyFamily.LONG_TERM
                and (self.timeframe is not Timeframe.DAY
                     or self.horizon not in (60, 120)))
        ):
            raise ValueError("invalid opportunity request")


@dataclass(frozen=True)
class OpportunityAssessment:
    state: AssessmentState
    reasons: tuple[str, ...]
    considered: tuple[Evidence, ...]
    supporting: tuple[Evidence, ...]
    contradicting: tuple[Evidence, ...]
    proposed_size_paise: None = None


def assess_opportunity(
    request: OpportunityRequest, evidence: tuple[Evidence, ...],
    *, candles: CandleAssessment | None = None,
) -> OpportunityAssessment:
    """Produce a research assessment; no ACCEPT state is earned in this phase."""
    reasons: set[str] = set()
    if not request.instrument.identity_verified:
        reasons.add("instrument identity unverified")
    if request.strategy is StrategyFamily.INTRADAY and candles is None:
        reasons.add("completed-candle validation missing")
    if candles is not None:
        reasons.update(candles.issues)
        reasons.update(candles.deferred)
        if (
            candles.instrument != request.instrument
            or candles.timeframe is not request.timeframe
            or candles.decision_at_utc != request.decision_at_utc
        ):
            reasons.add("candle assessment context mismatch")
    considered: list[Evidence] = []
    seen: set[tuple[EvidenceKind, str, str, int | None]] = set()
    waiting = bool(candles and (
        candles.deferred or set(candles.issues) & {
            "incomplete candle", "missing completed candle interval",
            "stale completed candle", "no completed candle by decision",
        }
    ))
    for item in evidence:
        if item.observed_at_utc > request.decision_at_utc:
            continue  # Future observations cannot influence even rejection reasons.
        if item.decision_cutoff_utc != request.decision_at_utc:
            reasons.add("decision cutoff mismatch")
            continue
        if item.instrument != request.instrument:
            reasons.add("instrument or exchange mismatch")
            continue
        if item.timeframe is not request.timeframe:
            reasons.add("evidence timeframe mismatch")
            continue
        if item.kind is EvidenceKind.PREDICTION and item.horizon != request.horizon:
            continue  # Distinct prediction horizons must not be pooled.
        if item.available_at_utc is None:
            reasons.add("evidence availability unknown")
            continue
        if item.available_at_utc > request.decision_at_utc:
            waiting = True
            reasons.add("evidence arrived after decision")
            continue
        considered.append(item)
        key = item.kind, item.provider, item.source_record_key, item.horizon
        if key in seen:
            reasons.add("duplicate evidence identity")
        seen.add(key)
        if item.quality is Quality.INVALID:
            reasons.add("invalid evidence quality")
        elif item.quality is Quality.UNVERIFIED:
            reasons.add("evidence quality unverified")
        if not item.usage_verified:
            reasons.add("evidence usage rights unverified")
    considered.sort(key=repr)
    kinds = {item.kind for item in considered}
    if EvidenceKind.PREDICTION not in kinds:
        reasons.add("prediction evidence missing")
    if EvidenceKind.TECHNICAL not in kinds:
        reasons.add("technical evidence missing")
    if EvidenceKind.FUNDAMENTAL not in kinds:
        reasons.add("fundamental evidence missing")
    if EvidenceKind.MARKET_REGIME not in kinds:
        reasons.add("market-regime evidence missing")
    if not any(item.kind is EvidenceKind.PREDICTION
               and item.calibrated_uncertainty is not None for item in considered):
        reasons.add("calibrated prediction uncertainty missing")
    reasons.add("allocation and statistical calibration not implemented")
    hard = {
        "instrument identity unverified", "instrument or exchange mismatch",
        "candle assessment context mismatch", "duplicate evidence identity",
        "invalid evidence quality", "decision cutoff mismatch",
        "evidence timeframe mismatch",
    }
    transient_candle_issues = {
        "incomplete candle", "missing completed candle interval",
        "stale completed candle", "no completed candle by decision",
    }
    if reasons & hard or candles is not None and (
        set(candles.issues) - transient_candle_issues
    ):
        state = AssessmentState.REJECT
    elif waiting:
        state = AssessmentState.WAIT
    else:
        state = AssessmentState.INSUFFICIENT_EVIDENCE
    return OpportunityAssessment(
        state, tuple(sorted(reasons)), tuple(considered),
        tuple(item for item in considered if item.stance is Stance.SUPPORTS),
        tuple(item for item in considered if item.stance is Stance.CONTRADICTS),
    )
