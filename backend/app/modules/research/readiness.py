"""Provider-neutral, read-only market-data readiness assessment.

This reports evidence gaps. It never grants a permit to the synthetic-only ML gate.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.intraday import ExchangeCalendar, ExchangeSession


class Eligibility(StrEnum):
    READY_FOR_RESEARCH = "READY_FOR_RESEARCH"
    INSUFFICIENT_COVERAGE = "INSUFFICIENT_COVERAGE"
    DATA_QUALITY_FAILURE = "DATA_QUALITY_FAILURE"
    CORPORATE_ACTION_UNVERIFIED = "CORPORATE_ACTION_UNVERIFIED"
    POINT_IN_TIME_UNVERIFIED = "POINT_IN_TIME_UNVERIFIED"
    LICENSING_UNVERIFIED = "LICENSING_UNVERIFIED"
    PROVIDER_ACCESS_BLOCKED = "PROVIDER_ACCESS_BLOCKED"


@dataclass(frozen=True)
class TradingHalt:
    """A verified interval during which candles are not expected."""

    start_utc: datetime
    end_utc: datetime

    def __post_init__(self) -> None:
        if (
            not _utc(self.start_utc)
            or not _utc(self.end_utc)
            or self.end_utc <= self.start_utc
        ):
            raise ValueError("halt needs an ordered explicit UTC interval")


@dataclass(frozen=True)
class CalendarEvidence:
    calendar: ExchangeCalendar
    halts: tuple[TradingHalt, ...] = ()
    source_note: str = ""

    def __post_init__(self) -> None:
        if not self.source_note.strip():
            raise ValueError("calendar evidence requires a source note")
        for halt in self.halts:
            if not any(
                session.opens_utc <= halt.start_utc < halt.end_utc <= session.closes_utc
                for session in self.calendar.sessions
            ):
                raise ValueError("halt is outside a verified session")


@dataclass(frozen=True)
class ReadinessBar:
    instrument_id: int
    symbol: str
    timeframe: Timeframe
    session: date
    open_paise: int
    high_paise: int
    low_paise: int
    close_paise: int
    volume: int
    provider: str
    source_record_key: str
    identity_verified: bool
    adjustment_status: str
    available_at_utc: datetime | None
    event_at_utc: datetime | None = None
    retrieved_at_utc: datetime | None = None
    start_utc: datetime | None = None
    end_utc: datetime | None = None
    is_complete: bool = True
    provenance: str = "source"
    reconciliation: str = "selected"


@dataclass(frozen=True)
class ReadinessPolicy:
    strategy: StrategyFamily
    timeframe: Timeframe
    min_sessions: int
    calendar: CalendarEvidence | None = None
    licensing_evidence: str | None = None
    historical_universe_evidence: str | None = None
    fundamentals_evidence: str | None = None
    provider_access_blocked: bool = False
    as_of_date: date | None = None

    def __post_init__(self) -> None:
        if self.strategy is StrategyFamily.INTRADAY:
            if self.timeframe is Timeframe.DAY:
                raise ValueError("intraday needs a minute timeframe")
        elif self.timeframe is not Timeframe.DAY:
            raise ValueError("swing and long term require daily bars")
        if type(self.min_sessions) is not int or self.min_sessions <= 0:
            raise ValueError("minimum sessions must be positive")


@dataclass(frozen=True)
class ReadinessReport:
    strategy: StrategyFamily
    timeframe: Timeframe
    available_instruments: tuple[str, ...]
    instrument_coverage: dict[str, dict[str, object]]
    observed_instruments: int
    observations: int
    first_date: date | None
    last_date: date | None
    providers: tuple[str, ...]
    provenance_counts: dict[str, int]
    missing_sessions: int | None
    missing_candles: int | None
    duplicate_observations: int
    mixed_provider_slots: int
    quality_issues: tuple[str, ...]
    statuses: tuple[Eligibility, ...]
    reasons: tuple[str, ...]
    next_actions: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return self.statuses == (Eligibility.READY_FOR_RESEARCH,)

    def as_dict(self) -> dict[str, object]:
        return {
            "strategy": self.strategy.value,
            "timeframe": self.timeframe.value,
            "available_instruments": self.available_instruments,
            "instrument_coverage": self.instrument_coverage,
            "observed_instruments": self.observed_instruments,
            "observations": self.observations,
            "first_date": self.first_date.isoformat() if self.first_date else None,
            "last_date": self.last_date.isoformat() if self.last_date else None,
            "providers": self.providers,
            "provenance_counts": self.provenance_counts,
            "missing_sessions": self.missing_sessions,
            "missing_candles": self.missing_candles,
            "duplicate_observations": self.duplicate_observations,
            "mixed_provider_slots": self.mixed_provider_slots,
            "quality_issue_count": len(self.quality_issues),
            "quality_issue_examples": self.quality_issues[:20],
            "statuses": tuple(status.value for status in self.statuses),
            "eligible": self.eligible,
            "reasons": self.reasons,
            "next_actions": self.next_actions,
            "training_enabled": False,
        }


def _utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timedelta(0)


def _slots(session: ExchangeSession, timeframe: Timeframe) -> tuple[datetime, ...]:
    if timeframe is Timeframe.DAY:
        return (session.closes_utc,)
    step = timedelta(minutes=timeframe.minutes)
    slots: list[datetime] = []
    current = session.opens_utc
    while current + step <= session.closes_utc:
        slots.append(current)
        current += step
    return tuple(slots)


def _halted(start: datetime, end: datetime, halts: tuple[TradingHalt, ...]) -> bool:
    return any(halt.start_utc <= start and end <= halt.end_utc for halt in halts)


def assess_readiness(
    bars: tuple[ReadinessBar, ...],
    policy: ReadinessPolicy,
    *,
    available_instruments: tuple[str, ...] = (),
    external_issues: tuple[str, ...] = (),
) -> ReadinessReport:
    """Assess every known failure without assuming weekdays are sessions."""
    statuses: set[Eligibility] = set()
    reasons: list[str] = []
    issues: list[str] = list(external_issues)
    providers = tuple(sorted({bar.provider for bar in bars if bar.provider}))
    provenance = dict(sorted(Counter(bar.provenance for bar in bars).items()))
    dates = sorted({bar.session for bar in bars})
    instrument_ids = {bar.instrument_id for bar in bars}
    grouped_by_symbol: dict[str, list[ReadinessBar]] = defaultdict(list)
    for bar in bars:
        grouped_by_symbol[bar.symbol].append(bar)
    instrument_coverage: dict[str, dict[str, object]] = {}
    for symbol, observed in sorted(grouped_by_symbol.items()):
        observed_dates = sorted({bar.session for bar in observed})
        instrument_coverage[symbol] = {
            "first_date": observed_dates[0].isoformat(),
            "last_date": observed_dates[-1].isoformat(),
            "sessions": len(observed_dates),
            "observations": len(observed),
            "providers": tuple(sorted({bar.provider for bar in observed})),
        }
    if not bars:
        statuses.add(Eligibility.INSUFFICIENT_COVERAGE)
        reasons.append("no observations for this timeframe")
    if policy.calendar is None:
        statuses.add(Eligibility.POINT_IN_TIME_UNVERIFIED)
        reasons.append(
            "authoritative exchange calendar, holidays and halts not supplied"
        )
    if not policy.licensing_evidence:
        statuses.add(Eligibility.LICENSING_UNVERIFIED)
        reasons.append("retention and ML-training rights not verified")
    if not policy.historical_universe_evidence:
        statuses.add(Eligibility.POINT_IN_TIME_UNVERIFIED)
        reasons.append("historical universe membership and survivorship not verified")
    if policy.strategy is StrategyFamily.LONG_TERM and not policy.fundamentals_evidence:
        statuses.add(Eligibility.INSUFFICIENT_COVERAGE)
        reasons.append("point-in-time fundamental data is unavailable")
    if policy.provider_access_blocked:
        statuses.add(Eligibility.PROVIDER_ACCESS_BLOCKED)
        reasons.append("selected provider access is blocked; no access assumed")

    slots: dict[tuple[int, date, datetime | None], list[ReadinessBar]] = defaultdict(
        list
    )
    sessions_by_instrument: dict[int, set[date]] = defaultdict(set)
    observed_slots: set[tuple[int, date, datetime | None]] = set()
    for bar in bars:
        if policy.as_of_date is not None and bar.session > policy.as_of_date:
            issues.append(f"{bar.symbol} {bar.session}: future session")
        sessions_by_instrument[bar.instrument_id].add(bar.session)
        slot = bar.start_utc if policy.timeframe is not Timeframe.DAY else None
        key = bar.instrument_id, bar.session, slot
        slots[key].append(bar)
        observed_slots.add(key)
        if bar.timeframe is not policy.timeframe:
            issues.append(f"{bar.symbol} {bar.session}: wrong timeframe")
        if (
            type(bar.instrument_id) is not int
            or bar.instrument_id <= 0
            or not bar.symbol
            or not bar.provider
            or not bar.source_record_key
            or any(
                type(v) is not int or v <= 0
                for v in (
                    bar.open_paise,
                    bar.high_paise,
                    bar.low_paise,
                    bar.close_paise,
                )
            )
            or bar.low_paise > min(bar.open_paise, bar.close_paise)
            or bar.high_paise < max(bar.open_paise, bar.close_paise)
            or type(bar.volume) is not int
            or bar.volume < 0
        ):
            issues.append(f"{bar.symbol} {bar.session}: invalid identity or OHLCV")
        if not bar.identity_verified:
            issues.append(f"{bar.symbol} {bar.session}: instrument mapping unverified")
        if bar.reconciliation == "conflict":
            issues.append(f"{bar.symbol} {bar.session}: unresolved provider conflict")
        if bar.adjustment_status not in ("verified_no_action", "verified_adjusted"):
            statuses.add(Eligibility.CORPORATE_ACTION_UNVERIFIED)
        if bar.available_at_utc is None:
            statuses.add(Eligibility.POINT_IN_TIME_UNVERIFIED)
        elif not _utc(bar.available_at_utc):
            issues.append(f"{bar.symbol} {bar.session}: invalid availability timezone")
        for label, timestamp in (
            ("event", bar.event_at_utc),
            ("retrieval", bar.retrieved_at_utc),
        ):
            if timestamp is not None and not _utc(timestamp):
                issues.append(f"{bar.symbol} {bar.session}: invalid {label} timezone")
        if (
            bar.available_at_utc is not None
            and bar.retrieved_at_utc is not None
            and _utc(bar.available_at_utc)
            and _utc(bar.retrieved_at_utc)
            and bar.retrieved_at_utc < bar.available_at_utc
        ):
            issues.append(f"{bar.symbol} {bar.session}: retrieved before availability")
        if (
            bar.event_at_utc is not None
            and _utc(bar.event_at_utc)
            and bar.event_at_utc.astimezone(ZoneInfo("Asia/Kolkata")).date()
            != bar.session
        ):
            issues.append(f"{bar.symbol} {bar.session}: event date differs")
        if policy.timeframe is Timeframe.DAY:
            if policy.calendar is not None:
                session = next(
                    (
                        s
                        for s in policy.calendar.calendar.sessions
                        if s.day == bar.session
                    ),
                    None,
                )
                if session is None:
                    issues.append(f"{bar.symbol} {bar.session}: bar on non-session day")
                elif _halted(
                    session.opens_utc,
                    session.closes_utc,
                    policy.calendar.halts,
                ):
                    issues.append(f"{bar.symbol} {bar.session}: bar on full halt")
                elif (
                    bar.available_at_utc is not None
                    and _utc(bar.available_at_utc)
                    and bar.available_at_utc < session.closes_utc
                ):
                    issues.append(
                        f"{bar.symbol} {bar.session}: available before session close"
                    )
        else:
            if (
                bar.start_utc is None
                or bar.end_utc is None
                or not _utc(bar.start_utc)
                or not _utc(bar.end_utc)
                or bar.end_utc - bar.start_utc
                != timedelta(minutes=policy.timeframe.minutes)
                or bar.start_utc.astimezone(ZoneInfo("Asia/Kolkata")).date()
                != bar.session
                or not bar.is_complete
            ):
                issues.append(
                    f"{bar.symbol} {bar.session}: invalid/incomplete candle time"
                )
            elif (
                bar.available_at_utc is not None
                and _utc(bar.available_at_utc)
                and bar.available_at_utc < bar.end_utc
            ):
                issues.append(
                    f"{bar.symbol} {bar.session}: available before candle completed"
                )
            if (
                policy.calendar is not None
                and bar.start_utc is not None
                and bar.end_utc is not None
                and _utc(bar.start_utc)
                and _utc(bar.end_utc)
            ):
                session = next(
                    (
                        s
                        for s in policy.calendar.calendar.sessions
                        if s.day == bar.session
                    ),
                    None,
                )
                if (
                    session is None
                    or bar.start_utc not in _slots(session, policy.timeframe)
                    or _halted(bar.start_utc, bar.end_utc, policy.calendar.halts)
                ):
                    issues.append(f"{bar.symbol} {bar.session}: outside active session")

    duplicates = 0
    mixed = 0
    for (instrument_id, day, _), grouped in slots.items():
        by_provider = Counter(bar.provider for bar in grouped)
        duplicates += sum(count - 1 for count in by_provider.values() if count > 1)
        if sum(bar.reconciliation == "selected" for bar in grouped) != 1:
            issues.append(
                f"instrument {instrument_id} {day}: selection missing/ambiguous"
            )
        if len(by_provider) > 1:
            mixed += 1
            values = {
                (b.open_paise, b.high_paise, b.low_paise, b.close_paise, b.volume)
                for b in grouped
            }
            if len(values) > 1:
                issues.append(
                    f"instrument {instrument_id} {day}: providers disagree on OHLCV"
                )
    if duplicates:
        issues.append(f"{duplicates} duplicate provider/instrument/candle observations")

    missing_sessions: int | None = None
    missing_candles: int | None = None
    if policy.calendar is not None:
        missing_sessions = 0
        missing_candles = 0
        for instrument_id in instrument_ids:
            for session in policy.calendar.calendar.sessions:
                expected = _slots(session, policy.timeframe)
                if policy.timeframe is Timeframe.DAY:
                    expected = (
                        ()
                        if _halted(
                            session.opens_utc, session.closes_utc, policy.calendar.halts
                        )
                        else expected
                    )
                else:
                    step = timedelta(minutes=policy.timeframe.minutes)
                    expected = tuple(
                        slot
                        for slot in expected
                        if not _halted(slot, slot + step, policy.calendar.halts)
                    )
                absent = sum(
                    (
                        instrument_id,
                        session.day,
                        None if policy.timeframe is Timeframe.DAY else slot,
                    )
                    not in observed_slots
                    for slot in expected
                )
                missing_candles += absent
                missing_sessions += bool(absent)
        if missing_candles:
            statuses.add(Eligibility.INSUFFICIENT_COVERAGE)
            reasons.append(
                f"{missing_candles} candles missing in "
                f"{missing_sessions} instrument-sessions"
            )
    if (
        any(
            len(sessions) < policy.min_sessions
            for sessions in sessions_by_instrument.values()
        )
        or not bars
    ):
        statuses.add(Eligibility.INSUFFICIENT_COVERAGE)
        reasons.append(
            f"at least one instrument has fewer than {policy.min_sessions} sessions"
        )
    if issues:
        statuses.add(Eligibility.DATA_QUALITY_FAILURE)
        reasons.append(
            f"{len(issues)} quality discrepancies in identity, "
            "timestamps, duplicates or OHLCV"
        )
    if Eligibility.CORPORATE_ACTION_UNVERIFIED in statuses:
        reasons.append("corporate-action adjustment or no-action evidence missing")
    if any(bar.available_at_utc is None for bar in bars):
        reasons.append("source availability timestamps missing")
    if not statuses:
        statuses.add(Eligibility.READY_FOR_RESEARCH)
    order = tuple(item for item in Eligibility if item in statuses)
    actions = {
        Eligibility.INSUFFICIENT_COVERAGE: (
            "obtain complete history and verified session coverage"
        ),
        Eligibility.DATA_QUALITY_FAILURE: (
            "resolve observation, identity and timestamp discrepancies"
        ),
        Eligibility.CORPORATE_ACTION_UNVERIFIED: (
            "verify actions and adjustment basis per instrument/date"
        ),
        Eligibility.POINT_IN_TIME_UNVERIFIED: (
            "verify calendar, universe and source availability"
        ),
        Eligibility.LICENSING_UNVERIFIED: (
            "obtain written retention and ML-training rights"
        ),
        Eligibility.PROVIDER_ACCESS_BLOCKED: (
            "resolve provider access before acquisition"
        ),
    }
    return ReadinessReport(
        policy.strategy,
        policy.timeframe,
        tuple(sorted(set(available_instruments))),
        instrument_coverage,
        len(instrument_ids),
        len(bars),
        dates[0] if dates else None,
        dates[-1] if dates else None,
        providers,
        provenance,
        missing_sessions,
        missing_candles,
        duplicates,
        mixed,
        tuple(issues),
        order,
        tuple(reasons),
        tuple(actions[item] for item in order if item in actions),
    )
