"""Actual optional-library runs on invented arrays; no market data or I/O."""

import hashlib
import importlib
import json
import math
from pathlib import Path

import pytest

from app.cli import main
from app.modules.research import ml_cli
from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.ml_models import (
    Hyperparameters,
    MissingDependencyError,
    Task,
    _fit_numeric,
    fit_model,
    load_model,
)
from app.modules.research.ml_pipeline import (
    FixtureSpec,
    Preprocessor,
    chronological_partitions,
    make_trusted_synthetic,
    prepare_training,
    validate_dataset,
)
from app.modules.research.ml_registry import (
    list_experiments,
    load_experiment,
    save_experiment,
)


def _invented(task: Task) -> tuple[
    tuple[tuple[float, float], ...], tuple[float, ...]
]:
    features = tuple((float(index % 11 - 5), float(index % 7 - 3))
                     for index in range(56))
    if task == "classification":
        labels = tuple(float(first + second > 0) for first, second in features)
    else:
        labels = tuple(0.03 * first - 0.02 * second for first, second in features)
    return features, labels


@pytest.mark.parametrize("family,task", [
    ("lightgbm", "regression"), ("lightgbm", "classification"),
    ("xgboost", "regression"), ("xgboost", "classification"),
    ("catboost", "regression"), ("catboost", "classification"),
    ("ridge", "regression"), ("elastic_net", "regression"),
    ("logistic_regression", "classification"),
    ("random_forest", "regression"), ("random_forest", "classification"),
    ("extra_trees", "regression"), ("extra_trees", "classification"),
    ("hist_gradient_boosting", "regression"),
    ("hist_gradient_boosting", "classification"),
])
def test_actual_synthetic_fit_determinism_and_safe_reload(
    family: str, task: Task,
) -> None:
    features, labels = _invented(task)
    params: Hyperparameters = {"num_boost_round": 8} if family in (
        "lightgbm", "xgboost", "catboost"
    ) else ({"max_iter": 6} if family == "hist_gradient_boosting"
            else {"n_estimators": 6} if family in
            ("random_forest", "extra_trees") else {})
    try:
        first = _fit_numeric(
            family, task, features, labels, ("invented_a", "invented_b"),
            23, params,
        )
    except MissingDependencyError as exc:
        if family not in ("lightgbm", "xgboost"):
            raise
        pytest.skip(str(exc))
    second = _fit_numeric(
        family, task, features, labels, ("invented_a", "invented_b"),
        23, params,
    )
    sample = ((-3.0, 1.0), (2.0, -1.0), (4.0, 2.0))
    predicted = first.predict(sample)
    assert len(predicted) == len(sample)
    assert all(math.isfinite(value) for value in predicted)
    if task == "classification":
        assert all(0 <= value <= 1 for value in predicted)
    assert second.predict(sample) == pytest.approx(predicted, abs=1e-9)
    payload = first.serialize()
    restored = load_model(
        payload, expected_sha256=hashlib.sha256(payload).hexdigest()
    )
    assert restored.library_version == first.library_version
    assert restored.feature_names == ("invented_a", "invented_b")
    assert restored.predict(sample) == pytest.approx(predicted, abs=1e-8)
    with pytest.raises(ValueError, match="feature row width"):
        restored.predict(((1.0,),))


def test_native_artifact_refuses_different_library_version() -> None:
    features, labels = _invented("regression")
    fitted = _fit_numeric(
        "catboost", "regression", features, labels,
        ("invented_a", "invented_b"), 23, {"num_boost_round": 2},
    )
    payload = json.loads(fitted.serialize())
    payload["library_version"] = "incompatible-version"
    changed = json.dumps(payload).encode()
    with pytest.raises(ValueError, match="library version differs"):
        load_model(changed, expected_sha256=hashlib.sha256(changed).hexdigest())


def test_linear_artifact_rejects_nonfinite_or_wrong_width_state() -> None:
    features, labels = _invented("regression")
    fitted = _fit_numeric(
        "ridge", "regression", features, labels,
        ("invented_a", "invented_b"), 23,
    )
    payload = json.loads(fitted.serialize())
    payload["state"]["coefficients"] = [0.1]
    changed = json.dumps(payload).encode()
    with pytest.raises(ValueError, match="coefficients"):
        load_model(changed, expected_sha256=hashlib.sha256(changed).hexdigest())


def test_skops_artifact_refuses_untrusted_types_before_loading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sio = importlib.import_module("skops.io")

    features, labels = _invented("regression")
    fitted = _fit_numeric(
        "random_forest", "regression", features, labels,
        ("invented_a", "invented_b"), 23, {"n_estimators": 2},
    )
    artifact = fitted.serialize()
    monkeypatch.setattr(sio, "get_untrusted_types", lambda **_kwargs: ["builtins.eval"])
    monkeypatch.setattr(
        sio, "loads", lambda *_args, **_kwargs: pytest.fail("unsafe loader reached")
    )
    with pytest.raises(ValueError, match="unexpected types"):
        load_model(artifact, expected_sha256=hashlib.sha256(artifact).hexdigest())


@pytest.mark.parametrize("family", ["catboost", "logistic_regression",
                                     "random_forest"])
def test_optional_classifier_requires_both_classes(family: str) -> None:
    features, _labels = _invented("classification")
    with pytest.raises(ValueError, match="both training classes"):
        _fit_numeric(
            family, "classification", features, (1.0,) * len(features),
            ("invented_a", "invented_b"), 23,
        )


@pytest.mark.parametrize("family", ["catboost", "ridge", "random_forest"])
def test_actual_training_uses_trusted_synthetic_gate_and_registry(
    family: str, tmp_path: Path,
) -> None:
    spec = FixtureSpec(
        StrategyFamily.SWING, Timeframe.DAY, 5,
        seed=17, sessions=90, instruments=2,
    )
    data = validate_dataset(make_trusted_synthetic(spec), spec, min_samples=20)
    decisions = sorted({row.decision_at_utc for row in data.rows})
    partitions = chronological_partitions(
        data, decisions[40], decisions[65], min_partition_samples=1,
    )
    fitted = Preprocessor(("daily_return",)).fit(partitions.train)
    prepared = prepare_training(partitions.train, fitted)
    model = fit_model(
        family, "regression", prepared, seed=17,
        hyperparameters=(
            {"num_boost_round": 3} if family == "catboost"
            else {"n_estimators": 4} if family == "random_forest" else {}
        ),
    )
    original = model.predict(fitted.transform(partitions.test))
    artifact = model.serialize()
    root = tmp_path / "experiments"
    identifier = save_experiment(
        root, {"family": family, "fixture": "invented"},
        {"synthetic": True, "family": family}, artifact,
    )
    metadata, stored = load_experiment(root, identifier)
    assert metadata["synthetic"] is True
    assert stored == artifact
    assert stored is not None
    restored = load_model(
        stored, expected_sha256=hashlib.sha256(stored).hexdigest()
    )
    assert restored.predict(fitted.transform(partitions.test)) == pytest.approx(
        original, abs=1e-8,
    )


@pytest.mark.parametrize("family,task,scenario", [
    ("catboost", "regression", "uptrend"),
    ("catboost", "classification", "alternating"),
    ("lightgbm", "classification", "alternating"),
    ("xgboost", "classification", "alternating"),
    ("logistic_regression", "classification", "alternating"),
])
def test_native_cli_synthetic_train_and_reload(
    family: str, task: Task, scenario: str,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = tmp_path / "experiments"
    monkeypatch.setattr(ml_cli, "ML_EXPERIMENTS_ROOT", registry)
    command = [
        "research-ml-train-synthetic", "--sessions", "80",
        "--model-family", family, "--task", task,
        "--scenario", scenario,
    ]
    if family in ("catboost", "lightgbm", "xgboost"):
        command.extend(("--hyperparameters", '{"num_boost_round":3}'))
    assert main(command) == 0
    identifiers = list_experiments(registry)
    assert len(identifiers) == 1
    assert main(["research-ml-evaluate", identifiers[0]]) == 0


def test_alternating_fixture_has_both_classes_and_separate_fingerprint() -> None:
    base = FixtureSpec(
        StrategyFamily.SWING, Timeframe.DAY, 5,
        seed=17, sessions=80, instruments=2,
    )
    alternating = FixtureSpec(
        StrategyFamily.SWING, Timeframe.DAY, 5,
        seed=17, sessions=80, instruments=2, scenario="alternating",
    )
    old = validate_dataset(make_trusted_synthetic(base), base, min_samples=20)
    mixed = validate_dataset(
        make_trusted_synthetic(alternating), alternating, min_samples=20,
    )
    assert {row.positive_return_label for row in mixed.rows} == {0, 1}
    assert old.fingerprint != mixed.fingerprint
