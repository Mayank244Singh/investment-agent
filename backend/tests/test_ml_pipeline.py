"""Fail-closed synthetic-only ML pipeline tests; no real price or account data."""

import socket
import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.ml_pipeline import (
    FittedPreprocessor,
    FixtureSpec,
    Preprocessor,
    chronological_partitions,
    make_trusted_synthetic,
    prepare_training,
    require_training_permit,
    validate_dataset,
    walk_forward_folds,
)


def _swing(sessions: int = 65):
    spec = FixtureSpec(
        StrategyFamily.SWING, Timeframe.DAY, 5,
        seed=123, sessions=sessions,
    )
    return spec, validate_dataset(make_trusted_synthetic(spec), spec)


def _intraday():
    spec = FixtureSpec(
        StrategyFamily.INTRADAY, Timeframe.MINUTE_15, 60,
        seed=123, sessions=20,
    )
    return spec, validate_dataset(make_trusted_synthetic(spec), spec)


@pytest.mark.parametrize("strategy,timeframe,horizon", [
    (StrategyFamily.INTRADAY, Timeframe.DAY, 5),
    (StrategyFamily.INTRADAY, Timeframe.MINUTE_15, 5),
    (StrategyFamily.SWING, Timeframe.MINUTE_1, 5),
    (StrategyFamily.SWING, Timeframe.DAY, 60),
    (StrategyFamily.LONG_TERM, Timeframe.DAY, 10),
])
def test_strategy_timeframe_horizon_gate(strategy, timeframe, horizon) -> None:
    with pytest.raises(ValueError):
        FixtureSpec(strategy, timeframe, horizon, seed=1)


def test_synthetic_provenance_and_reproducible_fingerprint() -> None:
    spec, first = _swing()
    second = validate_dataset(make_trusted_synthetic(spec), spec)
    assert first == second
    assert first.schema_version == "synthetic-ml-v1"
    assert first.fixture_provenance == "trusted-synthetic-generator-v1:seed=123"
    assert len(first.fingerprint) == 64
    assert all(row.provenance for row in first.rows)
    assert all(row.label_available_at_utc <= first.built_at_utc
               for row in first.rows)
    changed = replace(spec, seed=124)
    assert validate_dataset(
        make_trusted_synthetic(changed), changed
    ).fingerprint != first.fingerprint


def test_arbitrary_flag_or_tampered_samples_cannot_pass_safety_gate() -> None:
    spec, _ = _swing()
    trusted = make_trusted_synthetic(spec)
    with pytest.raises(ValueError, match="untrusted"):
        validate_dataset({"synthetic": True}, spec)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="differs"):
        validate_dataset(replace(
            trusted, samples=trusted.samples + (trusted.samples[1],)
        ), spec)
    first = trusted.samples[10]
    injected = replace(
        first,
        features={**first.features, "target_return": first.target_return},
    )
    altered = list(trusted.samples)
    altered[10] = injected
    with pytest.raises(ValueError, match="differs"):
        validate_dataset(replace(trusted, samples=tuple(altered)), spec)
    altered[10] = replace(first, availability_times_utc=(None,))
    with pytest.raises(ValueError, match="differs"):
        validate_dataset(replace(trusted, samples=tuple(altered)), spec)


def test_long_term_classification_is_explicitly_derived_from_return() -> None:
    spec = FixtureSpec(
        StrategyFamily.LONG_TERM, Timeframe.DAY, 60,
        seed=1, sessions=100,
    )
    dataset = validate_dataset(make_trusted_synthetic(spec), spec)
    assert all(
        row.positive_return_label == int(row.target_return > 0)
        for row in dataset.rows
    )


def test_intraday_fixture_has_available_labels_and_global_order() -> None:
    spec, dataset = _intraday()
    assert spec.horizon == 60
    assert len(dataset.rows) >= 30
    assert tuple(row.decision_at_utc for row in dataset.rows) == tuple(
        sorted(row.decision_at_utc for row in dataset.rows)
    )
    assert all(row.label_end_utc > row.decision_at_utc for row in dataset.rows)


def test_chronological_partition_purges_across_instruments_and_cutoff() -> None:
    _, dataset = _swing()
    decisions = sorted({row.decision_at_utc for row in dataset.rows})
    partitions = chronological_partitions(
        dataset, decisions[20], decisions[38], timedelta(days=1),
    )
    assert partitions.train.rows
    assert partitions.validation.rows
    assert partitions.test.rows
    assert all(row.label_available_at_utc <= partitions.train_end_utc
               for row in partitions.train.rows)
    assert all(row.label_end_utc < min(
        item.decision_at_utc for item in partitions.validation.rows
    ) for row in partitions.train.rows)
    assert any("overlaps" in reason or "unavailable" in reason
               for _, reason in partitions.excluded)
    assert {row.instrument_id for row in partitions.train.rows} == {1, 2}


def test_walk_forward_rolling_expanding_and_final_holdout() -> None:
    _, dataset = _swing()
    expanding = walk_forward_folds(
        dataset, 20, 8, 6, step_size=6, mode="expanding",
        holdout_size=4, min_partition_samples=2,
    )
    rolling = walk_forward_folds(
        dataset, 20, 8, 6, step_size=6, mode="rolling",
        holdout_size=4, min_partition_samples=2,
    )
    assert len(expanding.folds) >= 3
    assert len(rolling.folds) == len(expanding.folds)
    assert expanding.holdout is not None
    holdout_start = min(
        item.decision_at_utc for item in expanding.holdout.rows
    )
    assert all(
        row.label_end_utc < holdout_start
        for fold in expanding.folds for row in fold.test.rows
    )
    assert len(expanding.folds[1].train.rows) > len(rolling.folds[1].train.rows)
    assert all(fold.train.cutoff_utc == fold.train_end_utc
               for fold in expanding.folds)


@pytest.mark.parametrize("strategy,timeframe,horizon,sizes", [
    (StrategyFamily.SWING, Timeframe.DAY, 5, (20, 12, 8, 8)),
    (StrategyFamily.SWING, Timeframe.DAY, 10, (30, 20, 8, 8)),
    (StrategyFamily.LONG_TERM, Timeframe.DAY, 60, (90, 80, 15, 15)),
    (StrategyFamily.LONG_TERM, Timeframe.DAY, 120, (150, 140, 20, 20)),
    (StrategyFamily.INTRADAY, Timeframe.MINUTE_15, 60, (10, 8, 4, 4)),
])
def test_default_fixture_supports_three_safe_folds_with_holdout(
    strategy, timeframe, horizon, sizes,
) -> None:
    spec = FixtureSpec(strategy, timeframe, horizon, seed=7)
    dataset = validate_dataset(make_trusted_synthetic(spec), spec)
    plan = walk_forward_folds(
        dataset, sizes[0], sizes[1], sizes[2], step_size=sizes[3],
        holdout_size=4,
    )
    assert len(plan.folds) >= 3
    assert plan.holdout is not None
    assert all(fold.train.rows and fold.validation.rows and fold.test.rows
               for fold in plan.folds)


def test_preprocessor_fits_train_only_and_preserves_feature_order() -> None:
    _, dataset = _swing()
    decisions = sorted({row.decision_at_utc for row in dataset.rows})
    partitions = chronological_partitions(dataset, decisions[20], decisions[38])
    names = ("volume_change", "daily_return")
    preprocessor = Preprocessor(names, missing_policy="median", scale=True)
    fitted = preprocessor.fit(partitions.train)
    assert fitted.feature_names == names
    assert fitted.dataset_fingerprint == dataset.fingerprint
    observed = [row.feature("volume_change") for row in partitions.train.rows]
    assert all(value is not None for value in observed)
    assert fitted.means[0] == sum(
        (value for value in observed if value is not None), start=0
    ) / len(partitions.train.rows)
    assert len(fitted.transform(partitions.validation)) == len(
        partitions.validation.rows
    )
    with pytest.raises(ValueError, match="trusted training slice"):
        preprocessor.fit(partitions.validation)


def test_missing_value_policy_and_saved_preprocessor_roundtrip() -> None:
    _, dataset = _intraday()
    decisions = sorted({row.decision_at_utc for row in dataset.rows})
    partitions = chronological_partitions(
        dataset, decisions[15], decisions[27]
    )
    name = ("return_15m",)
    with pytest.raises(ValueError, match="missing training feature"):
        Preprocessor(name, missing_policy="reject").fit(partitions.train)
    fitted = Preprocessor(name, missing_policy="median", scale=True).fit(
        partitions.train
    )
    assert fitted.medians[0].is_finite()
    restored = FittedPreprocessor.from_dict(fitted.to_dict(), dataset.fingerprint)
    assert restored.transform(partitions.test) == fitted.transform(partitions.test)
    with pytest.raises(ValueError, match="fitted trusted"):
        prepare_training(partitions.train, restored)
    with pytest.raises(ValueError):
        FittedPreprocessor.from_dict(
            {**fitted.to_dict(), "dataset_fingerprint": "wrong"},
            dataset.fingerprint,
        )


def test_direct_training_permit_rejects_raw_or_altered_data() -> None:
    _, dataset = _swing()
    decisions = sorted({row.decision_at_utc for row in dataset.rows})
    partitions = chronological_partitions(dataset, decisions[20], decisions[38])
    fitted = Preprocessor(("daily_return",), scale=True).fit(partitions.train)
    prepared = prepare_training(partitions.train, fitted)
    assert require_training_permit(prepared) is prepared
    with pytest.raises(ValueError, match="untrusted"):
        require_training_permit((prepared.x, prepared.y_regression))
    with pytest.raises(ValueError, match="untrusted"):
        require_training_permit(replace(
            prepared, y_regression=(123.0,) + prepared.y_regression[1:]
        ))
    with pytest.raises(ValueError, match="validated datasets"):
        replace(dataset, rows=dataset.rows[1:])
    with pytest.raises(ValueError, match="partition must come"):
        replace(partitions.train, rows=partitions.train.rows[1:])
    with pytest.raises(ValueError, match="fitted trusted"):
        prepare_training(
            partitions.train,
            replace(fitted, means=(fitted.means[0] + 1,)),
        )


def test_generator_does_not_access_network_or_database(monkeypatch) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("synthetic ML path accessed network or database")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    spec = FixtureSpec(
        StrategyFamily.INTRADAY, Timeframe.MINUTE_15, 60,
        seed=9, sessions=12,
    )
    dataset = validate_dataset(make_trusted_synthetic(spec), spec,
                               min_samples=10)
    assert dataset.rows
