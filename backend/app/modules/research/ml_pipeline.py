"""Synthetic-only ML dataset gate, chronology, preprocessing and fold contracts.

No provider, database, broker, order path or real-data training entry lives here.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from statistics import median
from typing import Literal, cast

from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.core import (
    ResearchConfig,
    ResearchObservation,
    ResearchSample,
    build_dataset,
)
from app.modules.research.intraday import (
    ExchangeCalendar,
    ExchangeSession,
    IntradayCandle,
    IntradayConfig,
    IntradaySample,
    build_intraday_dataset,
)

SCHEMA_VERSION = "synthetic-ml-v1"
_ISSUER = object()
_ALLOWED_WARMUP = frozenset({
    "missing required feature", "insufficient future sessions",
    "target crosses exchange session close", "missing target minute bars",
})


def _utc(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() == timedelta(0)


@dataclass(frozen=True)
class FixtureSpec:
    strategy: StrategyFamily
    timeframe: Timeframe
    horizon: int
    seed: int
    sessions: int | None = None
    instruments: int = 2
    scenario: Literal["uptrend", "alternating"] = "uptrend"

    def __post_init__(self) -> None:
        if (
            type(self.seed) is not int or not 0 <= self.seed < 2**32
            or self.sessions is not None and (
                type(self.sessions) is not int or self.sessions < 3
                or self.sessions > 1_000
            )
            or type(self.instruments) is not int
            or not 1 <= self.instruments <= 20
            or self.scenario not in ("uptrend", "alternating")
        ):
            raise ValueError("invalid trusted fixture specification")
        if self.strategy is StrategyFamily.INTRADAY:
            if self.timeframe not in (
                Timeframe.MINUTE_1, Timeframe.MINUTE_5, Timeframe.MINUTE_15
            ) or self.horizon not in (5, 15, 30, 60):
                raise ValueError("incompatible intraday timeframe or horizon")
            if self.horizon % self.timeframe.minutes:
                raise ValueError("horizon must align with candle duration")
        elif self.strategy is StrategyFamily.SWING:
            if self.timeframe is not Timeframe.DAY or self.horizon not in (5, 10):
                raise ValueError("swing requires daily 5/10-session horizon")
        elif self.strategy is StrategyFamily.LONG_TERM:
            if self.timeframe is not Timeframe.DAY or self.horizon not in (60, 120):
                raise ValueError("long term requires daily 60/120-session horizon")
        else:
            raise ValueError("unknown strategy")

    @property
    def session_count(self) -> int:
        if self.sessions is not None:
            return self.sessions
        if self.strategy is StrategyFamily.INTRADAY:
            return 20
        if self.strategy is StrategyFamily.SWING:
            return 100
        return 650 if self.horizon == 120 else 400


@dataclass(frozen=True)
class TrustedDataset:
    spec: FixtureSpec
    samples: tuple[ResearchSample | IntradaySample, ...]
    calendar: tuple[date, ...]
    decision_times_utc: dict[date, datetime]
    built_at_utc: datetime
    schema_version: str = SCHEMA_VERSION
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise ValueError("trusted datasets are issued only by fixture generator")


def _weekdays(count: int) -> tuple[date, ...]:
    days: list[date] = []
    day = date(2024, 1, 1)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return tuple(days)


def _make_daily(spec: FixtureSpec) -> TrustedDataset:
    days = _weekdays(spec.session_count)
    rng = random.Random(spec.seed)
    observations: list[ResearchObservation] = []
    for instrument in range(1, spec.instruments + 1):
        close = 10_000 + instrument * 1_000
        for index, day in enumerate(days):
            drift = (8 if spec.scenario == "uptrend" else
                     (16 if (index // 8) % 2 == 0 else -16))
            close += drift + rng.randrange(-3, 4)
            observations.append(ResearchObservation(
                instrument_id=instrument, session=day,
                close_paise=close, volume=100 + index + instrument,
                provider="trusted-synthetic-generator-v1",
                source_record_key=f"daily:{spec.seed}:{instrument}:{index}",
                available_at_utc=datetime.combine(day, time(11), UTC),
                adjustment_status="verified_no_action",
                identity_valid_from=days[0], identity_valid_to=days[-1],
                identity_verified=True, open_paise=close,
                high_paise=close + 10, low_paise=close - 10,
            ))
    decisions = {day: datetime.combine(day, time(12), UTC) for day in days}
    built_at = datetime.combine(days[-1] + timedelta(days=1), time(12), UTC)
    config = ResearchConfig(
        dataset_version=SCHEMA_VERSION, sessions=days,
        decision_times_utc=decisions, built_at_utc=built_at,
        window=2, required_features=("daily_return",),
        strategy=spec.strategy, timeframe=Timeframe.DAY,
        horizons_sessions=(spec.horizon,),
        compute_optional_features=False,
    )
    dataset = build_dataset(tuple(observations), config)
    return TrustedDataset(
        spec, dataset.samples, days, decisions, built_at, _issuer=_ISSUER
    )


def _make_intraday(spec: FixtureSpec) -> TrustedDataset:
    days = _weekdays(spec.session_count)
    sessions = tuple(
        ExchangeSession(day, time(9, 15), time(10, 45), special=True)
        for day in days
    )
    rng = random.Random(spec.seed)
    candles: list[IntradayCandle] = []
    for instrument in range(1, spec.instruments + 1):
        close = 10_000 + instrument * 1_000
        for session_number, session in enumerate(sessions):
            for index in range(90 // spec.timeframe.minutes):
                start = session.opens_utc + timedelta(
                    minutes=index * spec.timeframe.minutes
                )
                end = start + timedelta(minutes=spec.timeframe.minutes)
                bar_number = session_number * (90 // spec.timeframe.minutes) + index
                drift = (4 if spec.scenario == "uptrend" else
                         (8 if (bar_number // 8) % 2 == 0 else -8))
                close += drift + rng.randrange(-2, 3)
                candles.append(IntradayCandle(
                    instrument_id=instrument, timeframe=spec.timeframe,
                    start_utc=start, end_utc=end, available_at_utc=end,
                    is_complete=True, open_paise=close, high_paise=close + 10,
                    low_paise=close - 10, close_paise=close,
                    volume=100 + index + instrument,
                    provider="trusted-synthetic-generator-v1",
                    source_record_key=(
                        f"intraday:{spec.seed}:{instrument}:{session.day}:{index}"
                    ),
                    adjustment_status="verified_no_action",
                    identity_verified=True,
                ))
    built_at = sessions[-1].closes_utc + timedelta(days=1)
    config = IntradayConfig(
        dataset_version=SCHEMA_VERSION, calendar=ExchangeCalendar(sessions),
        timeframe=spec.timeframe, built_at_utc=built_at,
        horizons_minutes=(spec.horizon,),
    )
    dataset = build_intraday_dataset(tuple(candles), config)
    decisions = {
        session.day: session.closes_utc for session in sessions
    }
    return TrustedDataset(
        spec, dataset.samples, days, decisions, built_at, _issuer=_ISSUER
    )


def make_trusted_synthetic(spec: FixtureSpec) -> TrustedDataset:
    """Generate invented values internally; arbitrary JSON is never training input."""
    if not isinstance(spec, FixtureSpec):
        raise ValueError("a typed fixture specification is required")
    return _make_intraday(spec) if spec.strategy is StrategyFamily.INTRADAY else (
        _make_daily(spec)
    )


@dataclass(frozen=True)
class ValidatedRow:
    instrument_id: int
    decision_at_utc: datetime
    label_end_utc: datetime
    label_available_at_utc: datetime
    features: tuple[tuple[str, Decimal | None], ...]
    target_return: Decimal
    positive_return_label: int
    provenance: tuple[str, ...]

    def feature(self, name: str) -> Decimal | None:
        return dict(self.features)[name]


@dataclass(frozen=True)
class ValidatedDataset:
    spec: FixtureSpec
    rows: tuple[ValidatedRow, ...]
    feature_names: tuple[str, ...]
    fingerprint: str
    schema_version: str
    fixture_provenance: str
    built_at_utc: datetime
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            self._issuer is not _ISSUER
            or self.schema_version != SCHEMA_VERSION
            or self.fingerprint != _fingerprint(self.spec, self.rows)
        ):
            raise ValueError("validated datasets require the synthetic safety gate")


def _fingerprint(spec: FixtureSpec, rows: tuple[ValidatedRow, ...]) -> str:
    spec_record: dict[str, object] = {
        "strategy": spec.strategy.value, "timeframe": spec.timeframe.value,
        "horizon": spec.horizon, "seed": spec.seed,
        "sessions": spec.session_count, "instruments": spec.instruments,
    }
    if spec.scenario != "uptrend":
        spec_record["scenario"] = spec.scenario
    content = {
        "schema": SCHEMA_VERSION,
        "spec": spec_record,
        "rows": [{
            "instrument_id": row.instrument_id,
            "decision": row.decision_at_utc.isoformat(),
            "label_end": row.label_end_utc.isoformat(),
            "label_available": row.label_available_at_utc.isoformat(),
            "features": [(name, str(value) if value is not None else None)
                         for name, value in row.features],
            "target": str(row.target_return),
            "class": row.positive_return_label,
            "provenance": row.provenance,
        } for row in rows],
    }
    return hashlib.sha256(json.dumps(
        content, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def validate_dataset(
    dataset: TrustedDataset, spec: FixtureSpec, min_samples: int = 30,
) -> ValidatedDataset:
    """Regenerate and verify the trusted fixture, then fail closed on all facts."""
    if (
        not isinstance(dataset, TrustedDataset)
        or dataset._issuer is not _ISSUER
        or not isinstance(spec, FixtureSpec)
        or dataset.spec != spec or dataset.schema_version != SCHEMA_VERSION
        or type(min_samples) is not int or min_samples < 1
    ):
        raise ValueError("untrusted or unsupported research dataset")
    expected = make_trusted_synthetic(spec)
    if (
        dataset.samples != expected.samples or dataset.calendar != expected.calendar
        or dataset.decision_times_utc != expected.decision_times_utc
        or dataset.built_at_utc != expected.built_at_utc
    ):
        raise ValueError("fixture differs from trusted deterministic generator")
    rows: list[ValidatedRow] = []
    seen: set[tuple[int, datetime, int]] = set()
    feature_names: tuple[str, ...] | None = None
    for sample in dataset.samples:
        if sample.rejection_reasons:
            if not set(sample.rejection_reasons) <= _ALLOWED_WARMUP:
                raise ValueError("unsafe research quality rejection")
            continue
        if (
            sample.dataset_version != SCHEMA_VERSION
            or sample.adjustment_status not in (
                "verified_no_action", "verified_adjusted"
            )
            or sample.instrument_id < 1
            or sample.target_return is None
            or not sample.target_return.is_finite()
            or not sample.provenance
            or any(not item.startswith("trusted-synthetic-generator-v1:")
                   for item in sample.provenance)
        ):
            raise ValueError("unsafe sample metadata or target")
        decision = sample.decision_at_utc.astimezone(UTC)
        if not _utc(decision) or decision > dataset.built_at_utc:
            raise ValueError("invalid decision timestamp")
        if any(
            item is None or not _utc(item) or item > decision
            for item in sample.availability_times_utc
        ):
            raise ValueError("feature availability violates decision time")
        if isinstance(sample, ResearchSample):
            if (
                sample.strategy is not spec.strategy
                or sample.timeframe is not spec.timeframe
                or sample.target_horizon_sessions != spec.horizon
                or sample.label_end_date not in dataset.decision_times_utc
                or sample.label_available_at_utc is None
            ):
                raise ValueError("daily sample strategy, horizon or label invalid")
            assert sample.label_end_date is not None
            label_end = dataset.decision_times_utc[sample.label_end_date]
            label_available = sample.label_available_at_utc
        else:
            if (
                spec.strategy is not StrategyFamily.INTRADAY
                or sample.timeframe is not spec.timeframe
                or sample.target_horizon_minutes != spec.horizon
                or sample.target_end_utc is None
                or sample.target_available_at_utc is None
            ):
                raise ValueError("intraday sample strategy or label invalid")
            label_end = sample.target_end_utc
            label_available = sample.target_available_at_utc
        label_end = label_end.astimezone(UTC)
        label_available = label_available.astimezone(UTC)
        if (
            label_end <= decision or label_available > dataset.built_at_utc
            or not _utc(label_end) or not _utc(label_available)
        ):
            raise ValueError("label window or availability invalid")
        names = tuple(sorted(sample.features))
        if (
            not names or any(
                "target" in name or "label" in name for name in names
            )
            or names != feature_names and feature_names is not None
        ):
            raise ValueError("feature schema contains a target or changed order")
        feature_names = names
        if any(
            value is not None and (
                not isinstance(value, Decimal) or not value.is_finite()
            ) for value in sample.features.values()
        ):
            raise ValueError("nonfinite research feature")
        missing = tuple(
            name for name, value in sample.features.items() if value is None
        )
        if sample.missing_features != missing:
            raise ValueError("inconsistent feature missingness")
        key = sample.instrument_id, decision, spec.horizon
        if key in seen:
            raise ValueError("duplicate or conflicting research sample")
        seen.add(key)
        rows.append(ValidatedRow(
            instrument_id=sample.instrument_id, decision_at_utc=decision,
            label_end_utc=label_end,
            label_available_at_utc=label_available,
            features=tuple((name, sample.features[name]) for name in names),
            target_return=sample.target_return,
            positive_return_label=int(sample.target_return > 0),
            provenance=sample.provenance,
        ))
    if len(rows) < min_samples or feature_names is None:
        raise ValueError("insufficient validated synthetic samples")
    ordered = tuple(sorted(rows, key=lambda item: (
        item.decision_at_utc, item.instrument_id
    )))
    return ValidatedDataset(
        spec=spec, rows=ordered, feature_names=feature_names,
        fingerprint=_fingerprint(spec, ordered),
        schema_version=SCHEMA_VERSION,
        fixture_provenance=(
            f"trusted-synthetic-generator-v1:seed={spec.seed}"
        ), built_at_utc=dataset.built_at_utc, _issuer=_ISSUER,
    )


@dataclass(frozen=True)
class PartitionSlice:
    phase: Literal["train", "validation", "test", "holdout"]
    rows: tuple[ValidatedRow, ...]
    dataset_fingerprint: str
    cutoff_utc: datetime
    _issuer: object = field(default=None, repr=False, compare=False)
    _seal: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            self._issuer is not _ISSUER
            or self._seal != _partition_digest(
                self.phase, self.rows, self.dataset_fingerprint,
                self.cutoff_utc,
            )
        ):
            raise ValueError("partition must come from validated synthetic data")


@dataclass(frozen=True)
class Partitions:
    train: PartitionSlice
    validation: PartitionSlice
    test: PartitionSlice
    excluded: tuple[tuple[ValidatedRow, str], ...]
    train_end_utc: datetime
    validation_end_utc: datetime


def _partition_digest(
    phase: str, rows: tuple[ValidatedRow, ...],
    dataset_fingerprint: str, cutoff: datetime,
) -> str:
    payload = {
        "phase": phase, "dataset_fingerprint": dataset_fingerprint,
        "cutoff": cutoff.isoformat(),
        "rows": [repr(item) for item in rows],
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def _slice(
    phase: Literal["train", "validation", "test", "holdout"],
    rows: tuple[ValidatedRow, ...], dataset: ValidatedDataset, cutoff: datetime,
) -> PartitionSlice:
    return PartitionSlice(
        phase, rows, dataset.fingerprint, cutoff, _issuer=_ISSUER,
        _seal=_partition_digest(phase, rows, dataset.fingerprint, cutoff),
    )


def chronological_partitions(
    dataset: ValidatedDataset,
    train_end_utc: datetime,
    validation_end_utc: datetime,
    embargo: timedelta = timedelta(0),
    min_partition_samples: int = 1,
) -> Partitions:
    """Purge globally across instruments and require labels at each cutoff."""
    if not isinstance(dataset, ValidatedDataset) or dataset._issuer is not _ISSUER:
        raise ValueError("only validated synthetic datasets can be partitioned")
    if dataset.fingerprint != _fingerprint(dataset.spec, dataset.rows):
        raise ValueError("validated dataset fingerprint changed")
    decisions = sorted({row.decision_at_utc for row in dataset.rows})
    if (
        not _utc(train_end_utc) or not _utc(validation_end_utc)
        or train_end_utc >= validation_end_utc
        or train_end_utc not in decisions
        or validation_end_utc not in decisions
        or embargo < timedelta(0)
        or type(min_partition_samples) is not int or min_partition_samples < 1
    ):
        raise ValueError("invalid chronological partition boundaries")
    validation_start = next(
        (item for item in decisions if item > train_end_utc), None
    )
    test_start = next(
        (item for item in decisions if item > validation_end_utc), None
    )
    if validation_start is None or test_start is None:
        raise ValueError("validation and test observations are required")
    train: list[ValidatedRow] = []
    validation: list[ValidatedRow] = []
    test: list[ValidatedRow] = []
    excluded: list[tuple[ValidatedRow, str]] = []
    for row in dataset.rows:
        at = row.decision_at_utc
        if at <= train_end_utc:
            if row.label_end_utc >= validation_start:
                excluded.append((row, "training label overlaps validation"))
            elif row.label_available_at_utc > train_end_utc:
                excluded.append((row, "training label unavailable at cutoff"))
            else:
                train.append(row)
        elif at <= train_end_utc + embargo:
            excluded.append((row, "train/validation embargo"))
        elif at <= validation_end_utc:
            if row.label_end_utc >= test_start:
                excluded.append((row, "validation label overlaps test"))
            elif row.label_available_at_utc > validation_end_utc:
                excluded.append((row, "validation label unavailable at cutoff"))
            else:
                validation.append(row)
        elif at <= validation_end_utc + embargo:
            excluded.append((row, "validation/test embargo"))
        else:
            test.append(row)
    if min(map(len, (train, validation, test))) < min_partition_samples:
        raise ValueError("partition has insufficient validated samples")
    return Partitions(
        _slice("train", tuple(train), dataset, train_end_utc),
        _slice("validation", tuple(validation), dataset, validation_end_utc),
        _slice("test", tuple(test), dataset, dataset.built_at_utc),
        tuple(excluded), train_end_utc, validation_end_utc,
    )


@dataclass(frozen=True)
class WalkForwardFold:
    index: int
    train: PartitionSlice
    validation: PartitionSlice
    test: PartitionSlice
    train_start_utc: datetime
    train_end_utc: datetime
    validation_end_utc: datetime
    test_end_utc: datetime
    excluded_count: int


@dataclass(frozen=True)
class WalkForwardPlan:
    folds: tuple[WalkForwardFold, ...]
    holdout: PartitionSlice | None


def walk_forward_folds(
    dataset: ValidatedDataset,
    train_size: int,
    validation_size: int,
    test_size: int,
    step_size: int | None = None,
    mode: Literal["expanding", "rolling"] = "expanding",
    embargo: timedelta = timedelta(0),
    holdout_size: int = 0,
    min_partition_samples: int = 1,
) -> WalkForwardPlan:
    """Window sizes count distinct decision times, never rows/calendar days."""
    if not isinstance(dataset, ValidatedDataset) or dataset._issuer is not _ISSUER:
        raise ValueError("walk-forward requires validated synthetic data")
    if dataset.fingerprint != _fingerprint(dataset.spec, dataset.rows):
        raise ValueError("validated dataset fingerprint changed")
    step = test_size if step_size is None else step_size
    if (
        any(type(value) is not int or value < 1 for value in (
            train_size, validation_size, test_size, step, min_partition_samples
        ))
        or type(holdout_size) is not int or holdout_size < 0
        or mode not in ("expanding", "rolling")
        or embargo < timedelta(0)
    ):
        raise ValueError("invalid walk-forward configuration")
    decisions = sorted({row.decision_at_utc for row in dataset.rows})
    if holdout_size >= len(decisions):
        raise ValueError("holdout consumes every decision time")
    holdout_start = (
        decisions[-holdout_size] if holdout_size else None
    )
    available = decisions[:-holdout_size] if holdout_size else decisions
    folds: list[WalkForwardFold] = []
    train_end_index = train_size - 1
    while train_end_index + validation_size + test_size < len(available):
        train_start_index = (
            0 if mode == "expanding" else train_end_index - train_size + 1
        )
        train_start = available[train_start_index]
        train_end = available[train_end_index]
        validation_end = available[train_end_index + validation_size]
        test_end = available[train_end_index + validation_size + test_size]
        try:
            base = chronological_partitions(
                dataset, train_end, validation_end, embargo,
                min_partition_samples=1,
            )
        except ValueError as exc:
            if str(exc) != "partition has insufficient validated samples":
                raise
            train_end_index += step
            continue
        train_rows = tuple(
            row for row in base.train.rows
            if row.decision_at_utc >= train_start
        )
        test_rows = tuple(
            row for row in base.test.rows
            if row.decision_at_utc <= test_end
            and (holdout_start is None or (
                row.label_end_utc < holdout_start
                and row.label_available_at_utc < holdout_start
            ))
        )
        if min(
            len(train_rows), len(base.validation.rows), len(test_rows)
        ) >= min_partition_samples:
            folds.append(WalkForwardFold(
                index=len(folds) + 1,
                train=_slice("train", train_rows, dataset, train_end),
                validation=base.validation,
                test=_slice("test", test_rows, dataset, test_end),
                train_start_utc=train_start, train_end_utc=train_end,
                validation_end_utc=validation_end, test_end_utc=test_end,
                excluded_count=len(base.excluded),
            ))
        train_end_index += step
    if not folds:
        raise ValueError("no safe walk-forward folds satisfy sample minimum")
    holdout = None
    if holdout_start is not None:
        holdout_rows = tuple(
            row for row in dataset.rows
            if row.decision_at_utc >= holdout_start
        )
        holdout = _slice(
            "holdout", holdout_rows, dataset, dataset.built_at_utc
        )
    return WalkForwardPlan(tuple(folds), holdout)


@dataclass(frozen=True)
class Preprocessor:
    feature_names: tuple[str, ...]
    missing_policy: Literal["reject", "median"] = "reject"
    scale: bool = False

    def __post_init__(self) -> None:
        if (
            not self.feature_names
            or len(set(self.feature_names)) != len(self.feature_names)
            or self.missing_policy not in ("reject", "median")
        ):
            raise ValueError("invalid preprocessing configuration")

    def fit(self, train: PartitionSlice) -> FittedPreprocessor:
        if (
            not isinstance(train, PartitionSlice)
            or train._issuer is not _ISSUER or train.phase != "train"
            or not train.rows
        ):
            raise ValueError("preprocessing must fit a trusted training slice")
        medians: list[Decimal] = []
        for name in self.feature_names:
            values = [row.feature(name) for row in train.rows]
            observed = [value for value in values if value is not None]
            if len(observed) != len(values) and self.missing_policy == "reject":
                raise ValueError(f"missing training feature: {name}")
            if not observed:
                raise ValueError(f"no training values for feature: {name}")
            medians.append(median(observed))
        matrix = _matrix(train.rows, self.feature_names, tuple(medians),
                         self.missing_policy)
        means: list[Decimal] = []
        scales: list[Decimal] = []
        for index, _ in enumerate(self.feature_names):
            column = [row[index] for row in matrix]
            mean = sum(column, Decimal(0)) / Decimal(len(column))
            variance = sum(
                ((value - mean) ** 2 for value in column), Decimal(0)
            ) / Decimal(len(column))
            deviation = variance.sqrt()
            means.append(mean if self.scale else Decimal(0))
            scales.append(deviation if self.scale and deviation else Decimal(1))
        return FittedPreprocessor(
            feature_names=self.feature_names, missing_policy=self.missing_policy,
            scale=self.scale, medians=tuple(medians), means=tuple(means),
            scales=tuple(scales), dataset_fingerprint=train.dataset_fingerprint,
            fitted_on_cutoff_utc=train.cutoff_utc, _issuer=_ISSUER,
            _can_train=True,
        )


def _matrix(
    rows: tuple[ValidatedRow, ...], names: tuple[str, ...],
    medians: tuple[Decimal, ...], policy: Literal["reject", "median"],
) -> tuple[tuple[Decimal, ...], ...]:
    result: list[tuple[Decimal, ...]] = []
    for row in rows:
        values: list[Decimal] = []
        for index, name in enumerate(names):
            value = row.feature(name)
            if value is None:
                if policy == "reject":
                    raise ValueError(f"missing feature: {name}")
                value = medians[index]
            if not value.is_finite():
                raise ValueError("nonfinite feature")
            values.append(value)
        result.append(tuple(values))
    return tuple(result)


@dataclass(frozen=True)
class FittedPreprocessor:
    feature_names: tuple[str, ...]
    missing_policy: Literal["reject", "median"]
    scale: bool
    medians: tuple[Decimal, ...]
    means: tuple[Decimal, ...]
    scales: tuple[Decimal, ...]
    dataset_fingerprint: str
    fitted_on_cutoff_utc: datetime
    _issuer: object = field(default=None, repr=False, compare=False)
    _can_train: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise ValueError("preprocessor must be fitted on synthetic training")

    def transform(
        self, partition: PartitionSlice,
    ) -> tuple[tuple[float, ...], ...]:
        if (
            not isinstance(partition, PartitionSlice)
            or partition._issuer is not _ISSUER
            or partition.dataset_fingerprint != self.dataset_fingerprint
        ):
            raise ValueError("partition does not match fitted synthetic dataset")
        decimal_matrix = _matrix(
            partition.rows, self.feature_names, self.medians,
            self.missing_policy,
        )
        result = tuple(tuple(float(
            (value - self.means[index]) / self.scales[index]
        ) for index, value in enumerate(row)) for row in decimal_matrix)
        if any(not math.isfinite(value) for row in result for value in row):
            raise ValueError("nonfinite transformed feature")
        return result

    def to_dict(self) -> dict[str, object]:
        return {
            "feature_names": list(self.feature_names),
            "missing_policy": self.missing_policy,
            "scale": self.scale,
            "medians": [str(value) for value in self.medians],
            "means": [str(value) for value in self.means],
            "scales": [str(value) for value in self.scales],
            "dataset_fingerprint": self.dataset_fingerprint,
            "fitted_on_cutoff_utc": self.fitted_on_cutoff_utc.isoformat(),
        }

    @classmethod
    def from_dict(
        cls, payload: dict[str, object], expected_dataset_fingerprint: str,
    ) -> FittedPreprocessor:
        """Rehydrate JSON data only for evaluation; never issue a fit permit."""
        expected_keys = {
            "feature_names", "missing_policy", "scale", "medians", "means",
            "scales", "dataset_fingerprint", "fitted_on_cutoff_utc",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise ValueError("invalid saved preprocessor fields")
        names = payload["feature_names"]
        policy = payload["missing_policy"]
        scale = payload["scale"]
        if (
            not isinstance(names, list) or not names
            or any(not isinstance(item, str) for item in names)
            or len(set(names)) != len(names)
            or policy not in ("reject", "median")
            or type(scale) is not bool
            or payload["dataset_fingerprint"] != expected_dataset_fingerprint
            or not isinstance(payload["fitted_on_cutoff_utc"], str)
        ):
            raise ValueError("invalid saved preprocessor metadata")
        arrays: list[tuple[Decimal, ...]] = []
        for key in ("medians", "means", "scales"):
            raw = payload[key]
            if (
                not isinstance(raw, list) or len(raw) != len(names)
                or any(not isinstance(item, str) for item in raw)
            ):
                raise ValueError("invalid saved preprocessor values")
            try:
                values = tuple(Decimal(item) for item in raw)
            except ArithmeticError as exc:
                raise ValueError("invalid saved preprocessor decimals") from exc
            if any(not value.is_finite() for value in values):
                raise ValueError("nonfinite saved preprocessor values")
            arrays.append(values)
        if any(value <= 0 for value in arrays[2]):
            raise ValueError("invalid saved preprocessor scale")
        timestamp = datetime.fromisoformat(payload["fitted_on_cutoff_utc"])
        if not _utc(timestamp):
            raise ValueError("preprocessor cutoff must be UTC")
        return cls(
            feature_names=tuple(names),
            missing_policy=cast(Literal["reject", "median"], policy),
            scale=scale, medians=arrays[0], means=arrays[1],
            scales=arrays[2], dataset_fingerprint=expected_dataset_fingerprint,
            fitted_on_cutoff_utc=timestamp, _issuer=_ISSUER,
            _can_train=False,
        )


@dataclass(frozen=True)
class SyntheticTrainingPermit:
    dataset_fingerprint: str
    matrix_target_digest: str
    _issuer: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._issuer is not _ISSUER:
            raise ValueError("training permit requires a trusted synthetic slice")


@dataclass(frozen=True)
class PreparedTraining:
    x: tuple[tuple[float, ...], ...]
    y_regression: tuple[float, ...]
    y_classification: tuple[int, ...]
    feature_names: tuple[str, ...]
    permit: SyntheticTrainingPermit


def _training_digest(
    x: tuple[tuple[float, ...], ...],
    y_regression: tuple[float, ...],
    y_classification: tuple[int, ...],
    feature_names: tuple[str, ...],
    dataset_fingerprint: str,
) -> str:
    payload = {
        "x": x, "y_regression": y_regression,
        "y_classification": y_classification,
        "feature_names": feature_names,
        "dataset_fingerprint": dataset_fingerprint,
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")).hexdigest()


def prepare_training(
    train: PartitionSlice, fitted: FittedPreprocessor,
) -> PreparedTraining:
    if (
        not isinstance(train, PartitionSlice) or train._issuer is not _ISSUER
        or train.phase != "train" or not train.rows
        or not isinstance(fitted, FittedPreprocessor)
        or fitted._issuer is not _ISSUER
        or not fitted._can_train
        or fitted.dataset_fingerprint != train.dataset_fingerprint
        or fitted.fitted_on_cutoff_utc != train.cutoff_utc
        or fitted != Preprocessor(
            fitted.feature_names, fitted.missing_policy, fitted.scale
        ).fit(train)
    ):
        raise ValueError("training requires a fitted trusted synthetic slice")
    x = fitted.transform(train)
    y_regression = tuple(float(row.target_return) for row in train.rows)
    y_classification = tuple(row.positive_return_label for row in train.rows)
    if any(not math.isfinite(value) for value in y_regression):
        raise ValueError("nonfinite training target")
    digest = _training_digest(
        x, y_regression, y_classification, fitted.feature_names,
        train.dataset_fingerprint,
    )
    permit = SyntheticTrainingPermit(
        train.dataset_fingerprint, digest, _issuer=_ISSUER
    )
    return PreparedTraining(
        x, y_regression, y_classification, fitted.feature_names, permit
    )


def require_training_permit(prepared: object) -> PreparedTraining:
    """Public fit APIs call this before accepting any data or parameters."""
    if (
        not isinstance(prepared, PreparedTraining)
        or not isinstance(prepared.permit, SyntheticTrainingPermit)
        or prepared.permit._issuer is not _ISSUER
        or not prepared.x or not prepared.feature_names
        or len(prepared.x) != len(prepared.y_regression)
        or len(prepared.x) != len(prepared.y_classification)
        or any(len(row) != len(prepared.feature_names) for row in prepared.x)
        or any(not math.isfinite(value) for row in prepared.x for value in row)
        or any(not math.isfinite(value) for value in prepared.y_regression)
        or prepared.permit.matrix_target_digest != _training_digest(
            prepared.x, prepared.y_regression, prepared.y_classification,
            prepared.feature_names, prepared.permit.dataset_fingerprint,
        )
    ):
        raise ValueError("untrusted or changed training data")
    return prepared
