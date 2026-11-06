"""Point-in-time research features, forward labels and quality reporting."""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.daily_features import (
    EXTRA_FEATURES,
    calculate_daily_features,
)

HORIZONS = (5, 10, 60, 120)  # Trading sessions, never calendar days.
BASE_FEATURE_NAMES = (
    "daily_return", "momentum", "volatility", "moving_average_paise",
    "price_to_moving_average", "volume_change", "average_volume",
    "drawdown", "relative_strength",
)
FEATURE_NAMES = BASE_FEATURE_NAMES + EXTRA_FEATURES
DEFAULT_REQUIRED_FEATURES = tuple(
    name for name in BASE_FEATURE_NAMES if name != "relative_strength"
)
AdjustmentStatus = Literal["verified_no_action", "verified_adjusted", "unverified"]


@dataclass(frozen=True)
class ResearchObservation:
    instrument_id: int
    session: date
    close_paise: int
    volume: int
    provider: str
    source_record_key: str
    available_at_utc: datetime | None
    adjustment_status: AdjustmentStatus
    identity_valid_from: date | None
    identity_valid_to: date | None
    identity_verified: bool
    corporate_action_flag: bool = False
    open_paise: int | None = None
    high_paise: int | None = None
    low_paise: int | None = None

    def __post_init__(self) -> None:
        if (
            type(self.instrument_id) is not int or self.instrument_id <= 0
            or type(self.close_paise) is not int or self.close_paise <= 0
            or type(self.volume) is not int or self.volume < 0
            or not self.provider or not self.source_record_key
            or self.adjustment_status not in {
                "verified_no_action", "verified_adjusted", "unverified"
            }
        ):
            raise ValueError("invalid research observation")
        if self.available_at_utc is not None and (
            self.available_at_utc.tzinfo is None
            or self.available_at_utc.utcoffset() is None
        ):
            raise ValueError("availability timestamp must include a timezone")
        if (
            self.identity_valid_from is not None
            and self.identity_valid_to is not None
            and self.identity_valid_to < self.identity_valid_from
        ):
            raise ValueError("invalid historical identity interval")
        ohlc = self.open_paise, self.high_paise, self.low_paise
        if any(value is not None for value in ohlc):
            if (
                any(type(value) is not int or value <= 0 for value in ohlc)
                or self.low_paise is None or self.high_paise is None
                or self.open_paise is None
                or self.low_paise > min(self.open_paise, self.close_paise)
                or self.high_paise < max(self.open_paise, self.close_paise)
            ):
                raise ValueError("invalid daily OHLCV")


@dataclass(frozen=True)
class ResearchConfig:
    dataset_version: str
    sessions: tuple[date, ...]
    decision_times_utc: dict[date, datetime]
    built_at_utc: datetime
    window: int = 5
    benchmark_instrument_id: int | None = None
    require_benchmark: bool = False
    required_features: tuple[str, ...] = DEFAULT_REQUIRED_FEATURES
    strategy: StrategyFamily | None = None
    timeframe: Timeframe = Timeframe.DAY
    horizons_sessions: tuple[int, ...] = HORIZONS
    compute_optional_features: bool = True

    def __post_init__(self) -> None:
        if (
            not self.dataset_version
            or self.window < 2
            or not self.sessions
            or self.sessions != tuple(sorted(set(self.sessions)))
            or set(self.decision_times_utc) != set(self.sessions)
            or self.built_at_utc.tzinfo is None
            or self.built_at_utc.utcoffset() is None
            or any(name not in FEATURE_NAMES for name in self.required_features)
            or self.timeframe is not Timeframe.DAY
            or (self.strategy is not None
                and not isinstance(self.strategy, StrategyFamily))
            or self.strategy is StrategyFamily.INTRADAY
            or not self.horizons_sessions
            or any(type(value) is not int or value <= 0
                   for value in self.horizons_sessions)
            or tuple(sorted(set(self.horizons_sessions))) != self.horizons_sessions
            or type(self.compute_optional_features) is not bool
        ):
            raise ValueError("invalid research configuration")
        if self.strategy is StrategyFamily.SWING and any(
            value not in (5, 10) for value in self.horizons_sessions
        ):
            raise ValueError("swing horizons must be 5 or 10 sessions")
        if self.strategy is StrategyFamily.LONG_TERM and any(
            value not in (60, 120) for value in self.horizons_sessions
        ):
            raise ValueError("long-term horizons must be 60 or 120 sessions")
        for session, timestamp in self.decision_times_utc.items():
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("decision timestamp must include a timezone")
            if timestamp.astimezone(ZoneInfo("Asia/Kolkata")).date() < session:
                raise ValueError("decision timestamp precedes its session")
            if timestamp.astimezone(UTC) > self.built_at_utc.astimezone(UTC):
                raise ValueError("dataset build precedes a decision timestamp")
        if self.require_benchmark and self.benchmark_instrument_id is None:
            raise ValueError("required benchmark instrument is missing")


@dataclass(frozen=True)
class ResearchSample:
    instrument_id: int
    feature_date: date
    decision_at_utc: datetime
    target_horizon_sessions: int
    label_end_date: date | None
    features: dict[str, Decimal | None]
    missing_features: tuple[str, ...]
    target_return: Decimal | None
    positive_return_label: int | None
    provenance: tuple[str, ...]
    availability_times_utc: tuple[datetime | None, ...]
    adjustment_status: AdjustmentStatus
    dataset_version: str
    rejection_reasons: tuple[str, ...]
    strategy: StrategyFamily | None = None
    timeframe: Timeframe = Timeframe.DAY
    label_available_at_utc: datetime | None = None

    @property
    def usable(self) -> bool:
        return not self.rejection_reasons


@dataclass(frozen=True)
class DatasetReport:
    instruments: int
    candidate_observations: int
    usable_observations: int
    first_date: date | None
    last_date: date | None
    missing_features: dict[str, int]
    invalid_labels: int
    corporate_action_or_adjustment_blockers: int
    unknown_historical_availability: int
    duplicate_observations: int
    conflicting_observations: int
    rejected_reasons: dict[str, int]


@dataclass(frozen=True)
class ResearchDataset:
    samples: tuple[ResearchSample, ...]
    report: DatasetReport


def _quality_reasons(
    observation: ResearchObservation, at: datetime, *, label: bool = False,
) -> list[str]:
    reasons: list[str] = []
    if not observation.identity_verified or (
        observation.identity_valid_from is None
        or observation.identity_valid_to is None
        or not observation.identity_valid_from
        <= observation.session
        <= observation.identity_valid_to
    ):
        reasons.append("historical identity unverified")
    if observation.adjustment_status == "unverified":
        reasons.append("adjustment status unverified")
    if observation.corporate_action_flag:
        reasons.append("corporate action unresolved")
    if observation.available_at_utc is None:
        reasons.append("historical availability unknown")
    elif observation.available_at_utc.astimezone(UTC) > at.astimezone(UTC):
        reasons.append(
            "label unavailable at dataset build" if label
            else "observation unavailable at decision time"
        )
    return reasons


def _ratio(numerator: int | Decimal, denominator: int | Decimal) -> Decimal:
    return Decimal(numerator) / Decimal(denominator)


def _features(
    history: list[ResearchObservation],
    benchmark: list[ResearchObservation] | None,
    window: int,
    extended_history: list[ResearchObservation],
    compute_optional_features: bool,
) -> dict[str, Decimal | None]:
    result: dict[str, Decimal | None] = dict.fromkeys(FEATURE_NAMES)
    if compute_optional_features:
        result.update(calculate_daily_features(extended_history))
    if len(history) < 2:
        return result
    current, previous = history[-1], history[-2]
    result["daily_return"] = _ratio(current.close_paise, previous.close_paise) - 1
    result["volume_change"] = (
        _ratio(current.volume, previous.volume) - 1
        if previous.volume > 0 else None
    )
    if len(history) < window:
        return result
    recent = history[-window:]
    closes = [item.close_paise for item in recent]
    mean_close = _ratio(sum(closes), window)
    result["moving_average_paise"] = mean_close
    result["price_to_moving_average"] = _ratio(current.close_paise, mean_close) - 1
    result["average_volume"] = _ratio(sum(item.volume for item in recent), window)
    result["drawdown"] = _ratio(current.close_paise, max(closes)) - 1
    if len(history) < window + 1:
        return result
    result["momentum"] = _ratio(
        current.close_paise, history[-window - 1].close_paise
    ) - 1
    returns = [
        _ratio(history[index].close_paise, history[index - 1].close_paise) - 1
        for index in range(len(history) - window, len(history))
    ]
    mean_return = sum(returns) / Decimal(window)
    result["volatility"] = (
        sum((value - mean_return) ** 2 for value in returns) / Decimal(window)
    ).sqrt()
    if benchmark is not None and len(benchmark) >= window + 1:
        benchmark_momentum = _ratio(
            benchmark[-1].close_paise, benchmark[-window - 1].close_paise
        ) - 1
        momentum = result["momentum"]
        assert momentum is not None
        result["relative_strength"] = momentum - benchmark_momentum
    return result


def build_dataset(
    observations: tuple[ResearchObservation, ...], config: ResearchConfig,
) -> ResearchDataset:
    """Reject unknown point-in-time facts; never infer them from ingestion time."""
    by_key: dict[tuple[int, date], ResearchObservation] = {}
    duplicates: set[tuple[int, date]] = set()
    conflicts: set[tuple[int, date]] = set()
    for item in observations:
        key = item.instrument_id, item.session
        previous = by_key.get(key)
        if previous is not None:
            duplicates.add(key)
            if (previous.close_paise, previous.volume) != (
                item.close_paise, item.volume
            ):
                conflicts.add(key)
        else:
            by_key[key] = item
    sessions = config.sessions
    instruments = sorted({
        item.instrument_id for item in observations
        if item.instrument_id != config.benchmark_instrument_id
    })
    samples: list[ResearchSample] = []
    missing_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for instrument_id in instruments:
        for current_index, session in enumerate(sessions):
            current = by_key.get((instrument_id, session))
            if current is None:
                continue
            start = max(0, current_index - config.window)
            history_keys = [
                (instrument_id, day) for day in sessions[start:current_index + 1]
            ]
            history = [by_key[key] for key in history_keys if key in by_key]
            benchmark: list[ResearchObservation] | None = None
            if config.benchmark_instrument_id is not None:
                benchmark_keys = [
                    (config.benchmark_instrument_id, day)
                    for day in sessions[start:current_index + 1]
                ]
                benchmark = [
                    by_key[key] for key in benchmark_keys if key in by_key
                ]
            decision = config.decision_times_utc[session]
            common_reasons: set[str] = set()
            if len(history) != len(history_keys):
                common_reasons.add("missing feature session")
            for item in history:
                common_reasons.update(_quality_reasons(item, decision))
                if (item.instrument_id, item.session) in duplicates:
                    common_reasons.add("duplicate source observation")
                if (item.instrument_id, item.session) in conflicts:
                    common_reasons.add("conflicting source observation")
            if benchmark is not None:
                if len(benchmark) != len(history_keys):
                    common_reasons.add("missing benchmark session")
                for item in benchmark:
                    common_reasons.update(_quality_reasons(item, decision))
                    if (item.instrument_id, item.session) in duplicates:
                        common_reasons.add("duplicate source observation")
                    if (item.instrument_id, item.session) in conflicts:
                        common_reasons.add("conflicting source observation")
            complete_benchmark = (
                benchmark if len(benchmark or []) == len(history_keys) else None
            )
            extended_history: list[ResearchObservation] = []
            if config.compute_optional_features:
                for prior_day in reversed(sessions[:current_index + 1]):
                    prior = by_key.get((instrument_id, prior_day))
                    if (
                        prior is None
                        or _quality_reasons(prior, decision)
                        or (instrument_id, prior_day) in duplicates
                        or (instrument_id, prior_day) in conflicts
                    ):
                        break
                    extended_history.append(prior)
                extended_history.reverse()
            feature_values = (
                dict.fromkeys(FEATURE_NAMES)
                if common_reasons
                else _features(
                    history, complete_benchmark, config.window,
                    extended_history, config.compute_optional_features,
                )
            )
            missing = tuple(
                name for name, value in feature_values.items() if value is None
            )
            missing_counts.update(missing)
            required_missing = set(missing) & set(config.required_features)
            if config.require_benchmark and "relative_strength" in missing:
                required_missing.add("relative_strength")
            if required_missing:
                common_reasons.add("missing required feature")
            for horizon in config.horizons_sessions:
                reasons = set(common_reasons)
                label_index = current_index + horizon
                label_end = (
                    sessions[label_index] if label_index < len(sessions) else None
                )
                label: Decimal | None = None
                positive: int | None = None
                label_available: datetime | None = None
                if label_end is None:
                    reasons.add("insufficient future sessions")
                else:
                    future_keys = [
                        (instrument_id, day)
                        for day in sessions[current_index + 1:label_index + 1]
                    ]
                    future = [by_key[key] for key in future_keys if key in by_key]
                    if len(future) == horizon:
                        label_available = future[-1].available_at_utc
                    if len(future) != horizon:
                        reasons.add("missing label session")
                    for item in future:
                        reasons.update(
                            _quality_reasons(item, config.built_at_utc, label=True)
                        )
                        if (item.instrument_id, item.session) in duplicates:
                            reasons.add("duplicate source observation")
                        if (item.instrument_id, item.session) in conflicts:
                            reasons.add("conflicting source observation")
                    if not reasons:
                        label = _ratio(future[-1].close_paise, current.close_paise) - 1
                        if horizon in (5, 10):
                            positive = int(label > 0)
                ordered_reasons = tuple(sorted(reasons))
                reason_counts.update(ordered_reasons)
                samples.append(ResearchSample(
                    instrument_id=instrument_id,
                    feature_date=session,
                    decision_at_utc=decision,
                    target_horizon_sessions=horizon,
                    label_end_date=label_end,
                    features=feature_values.copy(),
                    missing_features=missing,
                    target_return=label,
                    positive_return_label=positive,
                    provenance=tuple(
                        f"{item.provider}:{item.source_record_key}" for item in history
                    ),
                    availability_times_utc=tuple(
                        item.available_at_utc for item in history
                    ),
                    adjustment_status=current.adjustment_status,
                    dataset_version=config.dataset_version,
                    rejection_reasons=ordered_reasons,
                    strategy=config.strategy,
                    timeframe=config.timeframe,
                    label_available_at_utc=label_available,
                ))
    report = DatasetReport(
        instruments=len(instruments),
        candidate_observations=len(samples),
        usable_observations=sum(sample.usable for sample in samples),
        first_date=min((sample.feature_date for sample in samples), default=None),
        last_date=max((sample.feature_date for sample in samples), default=None),
        missing_features=dict(sorted(missing_counts.items())),
        invalid_labels=sum(
            bool(set(sample.rejection_reasons) & {
                "insufficient future sessions", "missing label session",
                "label unavailable at dataset build",
            }) for sample in samples
        ),
        corporate_action_or_adjustment_blockers=sum(
            bool(set(sample.rejection_reasons) & {
                "corporate action unresolved", "adjustment status unverified",
            }) for sample in samples
        ),
        unknown_historical_availability=sum(
            "historical availability unknown" in sample.rejection_reasons
            for sample in samples
        ),
        duplicate_observations=len(duplicates),
        conflicting_observations=len(conflicts),
        rejected_reasons=dict(sorted(reason_counts.items())),
    )
    return ResearchDataset(tuple(samples), report)
