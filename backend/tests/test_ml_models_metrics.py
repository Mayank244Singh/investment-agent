"""Synthetic-only model adapter and metric tests; no real data or I/O."""

import hashlib
import json
import math
from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest

from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.ml_metrics import (
    evaluate_classification,
    evaluate_regression,
)
from app.modules.research.ml_models import (
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


def test_constant_return_baseline_and_safe_round_trip() -> None:
    model = _fit_numeric(
        "constant_return", "regression", ((1.0,), (2.0,)),
        (1.0, 3.0), ("invented_feature",), 17,
    )
    assert model.predict(((99.0,), (0.0,))) == (2.0, 2.0)
    artifact = model.serialize()
    assert artifact.startswith(b"{")
    checksum = hashlib.sha256(artifact).hexdigest()
    loaded = load_model(artifact, expected_sha256=checksum)
    assert loaded.predict(((4.0,),)) == (2.0,)
    assert loaded.feature_names == ("invented_feature",)
    assert loaded.seed == 17
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_model(artifact + b" ", expected_sha256=checksum)
    with pytest.raises(ValueError, match="invalid model artifact JSON"):
        bad = b"\x80pickle payload"
        load_model(bad, expected_sha256=hashlib.sha256(bad).hexdigest())


def test_majority_and_momentum_baselines() -> None:
    majority = _fit_numeric(
        "majority_class", "classification", ((1.0,), (2.0,), (3.0,)),
        (0.0, 1.0, 1.0), ("momentum",), 3,
    )
    assert majority.predict(((0.0,),)) == (2 / 3,)
    momentum_regression = _fit_numeric(
        "momentum", "regression", ((0.1,), (-0.2,)),
        (0.2, -0.1), ("momentum",), 3,
    )
    assert momentum_regression.predict(((0.3,), (-0.4,))) == (0.3, -0.4)
    momentum_classifier = _fit_numeric(
        "momentum", "classification",
        ((0.1,), (0.2,), (-0.2,), (-0.1,)),
        (1.0, 0.0, 0.0, 0.0), ("momentum",), 3,
    )
    assert momentum_classifier.predict(((1.0,), (-1.0,))) == (0.5, 0.0)
    artifact = momentum_classifier.serialize()
    loaded = load_model(artifact, expected_sha256=hashlib.sha256(artifact).hexdigest())
    assert loaded.predict(((1.0,), (-1.0,))) == (0.5, 0.0)


@pytest.mark.parametrize("family,task", [
    ("constant_return", "classification"),
    ("majority_class", "regression"),
    ("unknown", "regression"),
])
def test_invalid_model_family_or_task_rejected(family: str, task: str) -> None:
    with pytest.raises(ValueError):
        _fit_numeric(family, cast(Task, task), ((1.0,),), (1.0,), ("momentum",), 1)


def test_feature_order_missing_momentum_and_nonfinite_rejected() -> None:
    with pytest.raises(ValueError, match="feature row width"):
        _fit_numeric("constant_return", "regression", ((1.0, 2.0),),
                     (1.0,), ("x",), 1)
    with pytest.raises(ValueError, match="finite"):
        _fit_numeric("constant_return", "regression", ((math.nan,),),
                     (1.0,), ("x",), 1)
    with pytest.raises(ValueError, match="momentum feature is absent"):
        _fit_numeric("momentum", "regression", ((1.0,),),
                     (1.0,), ("other",), 1)
    with pytest.raises(ValueError, match="feature names"):
        _fit_numeric("constant_return", "regression", ((1.0, 2.0),),
                     (1.0,), ("x", "x"), 1)
    model = _fit_numeric("constant_return", "regression", ((1.0, 2.0),),
                         (1.0,), ("first", "second"), 1)
    with pytest.raises(ValueError, match="feature row width"):
        model.predict(((1.0,),))


def test_optional_adapters_fail_clearly_without_dependencies(monkeypatch) -> None:
    import app.modules.research.ml_models as module

    original = module.importlib.import_module

    def missing(name: str):
        if name in ("lightgbm", "xgboost"):
            raise ModuleNotFoundError(name)
        return original(name)

    monkeypatch.setattr(module.importlib, "import_module", missing)
    for family in ("lightgbm", "xgboost"):
        with pytest.raises(MissingDependencyError, match=family):
            _fit_numeric(family, "regression", ((1.0,), (2.0,)),
                         (1.0, 2.0), ("invented",), 1)


@pytest.mark.parametrize("family", ["lightgbm", "xgboost"])
def test_optional_adapters_train_predict_and_load_native_artifact_with_mocks(
    family: str, monkeypatch,
) -> None:
    import app.modules.research.ml_models as module

    class FakeDataset:
        def __init__(self, matrix, label=None, feature_name=None,
                     free_raw_data=None, feature_names=None):
            self.matrix = matrix
            self.label = label

    class FakeBooster:
        def __init__(self, model_str: str | None = None):
            self.value = float(model_str) if model_str is not None else 0.0

        def predict(self, matrix):
            data = matrix.matrix if isinstance(matrix, FakeDataset) else matrix
            return [self.value] * len(data)

        def model_to_string(self):
            return str(self.value)

        def save_raw(self, raw_format: str):
            assert raw_format == "json"
            return json.dumps({"value": self.value}).encode("utf-8")

        def load_model(self, raw):
            self.value = float(json.loads(bytes(raw))["value"])

    def fake_train(settings, dataset, num_boost_round):
        assert settings["seed"] == 11
        assert num_boost_round == 2
        booster = FakeBooster()
        booster.value = sum(dataset.label) / len(dataset.label)
        return booster

    fake_numpy = SimpleNamespace(asarray=lambda value, dtype: value)
    fake_lightgbm = SimpleNamespace(
        Dataset=FakeDataset, train=fake_train, Booster=FakeBooster
    )
    fake_xgboost = SimpleNamespace(
        DMatrix=FakeDataset, train=fake_train, Booster=FakeBooster
    )
    modules = {
        "numpy": fake_numpy, "lightgbm": fake_lightgbm,
        "xgboost": fake_xgboost,
    }
    original = module.importlib.import_module
    monkeypatch.setattr(module.importlib, "import_module",
                        lambda name: modules.get(name) or original(name))
    monkeypatch.setattr("importlib.metadata.version", lambda _name: "mock-0")
    fitted = _fit_numeric(
        family, "regression", ((1.0,), (2.0,)), (0.2, 0.4),
        ("invented",), 11, {"num_boost_round": 2},
    )
    assert fitted.predict(((3.0,),)) == (pytest.approx(0.3),)
    artifact = fitted.serialize()
    loaded = load_model(
        artifact, expected_sha256=hashlib.sha256(artifact).hexdigest()
    )
    assert loaded.predict(((4.0,),)) == (pytest.approx(0.3),)


def test_model_artifact_schema_checks_after_checksum() -> None:
    model = _fit_numeric("majority_class", "classification", ((1.0,),),
                         (1.0,), ("invented",), 0)
    raw = json.loads(model.serialize())
    raw["state"]["prevalence"] = float("nan")
    assert model.serialize()  # original model remains valid; mutate only payload
    # The original model's serialization is unaffected; malformed JSON
    # constants are rejected even when a caller supplies its own digest.
    bad = json.dumps(raw).encode("utf-8")
    with pytest.raises(ValueError, match="invalid JSON number"):
        load_model(bad, expected_sha256=hashlib.sha256(bad).hexdigest())


def test_tampered_native_artifact_is_rejected_before_optional_parser(
    monkeypatch,
) -> None:
    import app.modules.research.ml_models as module

    payload = json.dumps({
        "format_version": 1, "family": "xgboost", "task": "regression",
        "feature_names": ["invented"], "seed": 0,
        "hyperparameters": {}, "state": {}, "library_version": "mock",
        "native_model": "dGFtcGVyZWQ=",
    }).encode("utf-8")

    def forbidden_import(_name: str):
        raise AssertionError("native parser reached before checksum validation")

    monkeypatch.setattr(module, "_optional_import", forbidden_import)
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_model(payload, expected_sha256="0" * 64)


def test_regression_metrics_and_undefined_cases() -> None:
    result = evaluate_regression((1.0, -1.0), (2.0, -2.0))
    assert result["mae"] == 1.0
    assert result["rmse"] == 1.0
    assert result["mean_prediction_error"] == 0.0
    assert result["directional_accuracy"] == 1.0
    assert result["correlation"] == 1.0
    assert all(value is None for value in evaluate_regression((), ()).values())
    assert evaluate_regression((1.0, 1.0), (2.0, 3.0))["correlation"] is None
    with pytest.raises(ValueError, match="finite"):
        evaluate_regression((1.0,), (math.inf,))
    with pytest.raises(ValueError, match="counts differ"):
        evaluate_regression((1.0,), ())


def test_classification_metrics_and_undefined_cases() -> None:
    result = evaluate_classification((0, 0, 1, 1), (0.1, 0.4, 0.6, 0.9))
    assert result["accuracy"] == 1.0
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
    assert result["f1"] == 1.0
    assert result["roc_auc"] == 1.0
    assert result["log_loss"] is not None
    assert result["brier_score"] == pytest.approx(0.085)
    assert all(value is None for value in evaluate_classification((), ()).values())
    assert evaluate_classification((1, 1), (0.6, 0.7))["roc_auc"] is None
    assert evaluate_classification((0, 1), (0.5, 0.5))["roc_auc"] == 0.5
    assert evaluate_classification((0, 1), (1.0, 0.0))["log_loss"] is None
    with pytest.raises(ValueError, match="probability"):
        evaluate_classification((0, 1), (0.1, 1.2))
    with pytest.raises(ValueError, match="binary"):
        evaluate_classification((0, 2), (0.1, 0.2))


def test_public_entry_point_rejects_arbitrary_numeric_data() -> None:
    with pytest.raises(ValueError, match="untrusted"):
        fit_model("constant_return", "regression", object(), 1)


def test_public_fit_requires_exact_trusted_synthetic_training_matrix() -> None:
    spec = FixtureSpec(
        StrategyFamily.SWING, Timeframe.DAY, 5,
        seed=1, sessions=80, instruments=1,
    )
    data = validate_dataset(make_trusted_synthetic(spec), spec, min_samples=5)
    decisions = sorted({row.decision_at_utc for row in data.rows})
    parts = chronological_partitions(
        data, decisions[35], decisions[55], min_partition_samples=1
    )
    prepared = prepare_training(
        parts.train, Preprocessor(("daily_return",)).fit(parts.train)
    )
    model = fit_model("constant_return", "regression", prepared, seed=7)
    assert len(model.predict(((0.1,),))) == 1
    modified = replace(prepared, x=((999.0,),) + prepared.x[1:])
    with pytest.raises(ValueError, match="untrusted or changed"):
        fit_model("constant_return", "regression", modified, seed=7)
