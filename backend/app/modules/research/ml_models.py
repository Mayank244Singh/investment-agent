"""Synthetic-research model adapters; no broker, database, or pickle path."""

import base64
import binascii
import hashlib
import hmac
import importlib
import json
import math
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Task = Literal["regression", "classification"]
Hyperparameters = dict[str, int | float | str]
FAMILIES = frozenset({
    "constant_return", "majority_class", "momentum", "lightgbm", "xgboost",
    "catboost", "ridge", "elastic_net", "logistic_regression",
    "random_forest", "extra_trees", "hist_gradient_boosting",
})
_LINEAR = frozenset({"ridge", "elastic_net", "logistic_regression"})
_NATIVE = frozenset({"lightgbm", "xgboost", "catboost"})
_SKLEARN_TREES = frozenset({
    "random_forest", "extra_trees", "hist_gradient_boosting",
})
_SKOPS_ALLOWED = {
    "random_forest": frozenset({"sklearn.tree._tree.Tree"}),
    "extra_trees": frozenset({"sklearn.tree._tree.Tree"}),
    "hist_gradient_boosting": frozenset({
        "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor"
    }),
}
_MAX_ARTIFACT_BYTES = 20_000_000


class MissingDependencyError(RuntimeError):
    """The requested named algorithm is unavailable; no fallback is used."""


def _optional_import(package: str) -> Any:
    try:
        return importlib.import_module(package)
    except (ImportError, OSError) as exc:
        raise MissingDependencyError(
            f"{package} or its native runtime is unavailable for this model"
        ) from exc
    except Exception as exc:
        if package != "xgboost" or type(exc).__name__ != "XGBoostError":
            raise
        raise MissingDependencyError(
            "xgboost or its native runtime is unavailable for this model"
        ) from exc


def _validate_X(
    X: tuple[tuple[float, ...], ...], feature_names: tuple[str, ...],
    *, allow_empty: bool = False,
) -> None:
    if not feature_names or len(set(feature_names)) != len(feature_names):
        raise ValueError("feature names must be nonempty and unique")
    if any(not isinstance(name, str) or not name.strip() for name in feature_names):
        raise ValueError("invalid feature name")
    if not X and not allow_empty:
        raise ValueError("training requires samples")
    for row in X:
        if len(row) != len(feature_names):
            raise ValueError("feature row width differs from feature names")
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for value in row):
            raise ValueError("model features must be finite numeric values")


def _validate_y(y: tuple[float, ...], task: Task) -> None:
    if any(type(value) not in (int, float) or not math.isfinite(value)
           for value in y):
        raise ValueError("training targets must be finite numeric values")
    if task == "classification" and any(value not in (0, 1) for value in y):
        raise ValueError("classification targets must be binary")


def _validated_hyperparameters(
    hyperparameters: Hyperparameters | None,
) -> Hyperparameters:
    values = dict(hyperparameters or {})
    for name, value in values.items():
        if not isinstance(name, str) or not name:
            raise ValueError("invalid hyperparameter name")
        if type(value) not in (int, float, str):
            raise ValueError(f"invalid hyperparameter {name}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"non-finite hyperparameter {name}")
    return values


@dataclass(frozen=True)
class FittedModel:
    family: str
    task: Task
    feature_names: tuple[str, ...]
    seed: int
    hyperparameters: Hyperparameters
    state: dict[str, Any]
    library_version: str | None = None
    _native_model: Any = field(default=None, repr=False, compare=False)

    def predict(self, X: tuple[tuple[float, ...], ...]) -> tuple[float, ...]:
        """Classification output is positive-class probability, not a trade."""
        _validate_X(X, self.feature_names, allow_empty=True)
        if not X:
            return ()
        if self.family == "constant_return":
            values = tuple(float(self.state["mean_return"]) for _ in X)
        elif self.family == "majority_class":
            values = tuple(float(self.state["prevalence"]) for _ in X)
        elif self.family == "momentum":
            index = self.feature_names.index(self.state["feature_name"])
            if self.task == "regression":
                values = tuple(float(row[index]) for row in X)
            else:
                values = tuple(
                    float(self.state["positive_prevalence"])
                    if row[index] > 0
                    else float(self.state["nonpositive_prevalence"])
                    for row in X
                )
        elif self.family in _LINEAR:
            coefficients = self.state["coefficients"]
            intercept = self.state["intercept"]
            scores = (intercept + sum(weight * value for weight, value in
                      zip(coefficients, row, strict=True)) for row in X)
            if self.task == "classification":
                values = tuple(
                    1 / (1 + math.exp(-score)) if score >= 0
                    else math.exp(score) / (1 + math.exp(score))
                    for score in scores
                )
            else:
                values = tuple(float(score) for score in scores)
        elif self.family in _NATIVE | _SKLEARN_TREES:
            if self._native_model is None:
                raise ValueError("native model is not loaded")
            numpy = _optional_import("numpy")
            matrix = numpy.asarray(X, dtype="float64")
            if self.family == "lightgbm":
                output = self._native_model.predict(matrix)
            elif self.family == "xgboost":
                xgboost = _optional_import("xgboost")
                output = self._native_model.predict(xgboost.DMatrix(
                    matrix, feature_names=list(self.feature_names)
                ))
            elif self.task == "classification":
                output = self._native_model.predict_proba(matrix)[:, 1]
            else:
                output = self._native_model.predict(matrix)
            values = tuple(float(value) for value in output)
        else:
            raise ValueError("unsupported model family")
        if len(values) != len(X) or any(not math.isfinite(value) for value in values):
            raise ValueError("model returned invalid predictions")
        if self.task == "classification" and any(
            not 0 <= value <= 1 for value in values
        ):
            raise ValueError("classifier returned invalid probabilities")
        return values

    def serialize(self) -> bytes:
        """Canonical JSON plus model-native text/JSON; never Python pickle."""
        native: str | None = None
        if self.family == "lightgbm":
            if self._native_model is None:
                raise ValueError("native model is not loaded")
            native = str(self._native_model.model_to_string())
        elif self.family == "xgboost":
            if self._native_model is None:
                raise ValueError("native model is not loaded")
            raw = self._native_model.save_raw(raw_format="json")
            native = base64.b64encode(bytes(raw)).decode("ascii")
        elif self.family == "catboost":
            if self._native_model is None:
                raise ValueError("native model is not loaded")
            with tempfile.TemporaryDirectory(prefix="research-model-") as folder:
                path = Path(folder) / "model.json"
                self._native_model.save_model(str(path), format="json")
                native = base64.b64encode(path.read_bytes()).decode("ascii")
        elif self.family in _SKLEARN_TREES:
            if self._native_model is None:
                raise ValueError("native model is not loaded")
            sio = _optional_import("skops.io")
            serialized = sio.dumps(self._native_model)
            if set(sio.get_untrusted_types(data=serialized)) - _SKOPS_ALLOWED[
                self.family
            ]:
                raise ValueError("skops artifact contains unexpected types")
            native = base64.b64encode(serialized).decode("ascii")
        payload = {
            "format_version": 1, "family": self.family, "task": self.task,
            "feature_names": list(self.feature_names), "seed": self.seed,
            "hyperparameters": self.hyperparameters, "state": self.state,
            "library_version": self.library_version, "native_model": native,
        }
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(encoded) > _MAX_ARTIFACT_BYTES:
            raise ValueError("model artifact exceeds 20 MB")
        return encoded


def fit_model(
    family: str,
    task: Task,
    prepared: object,
    seed: int,
    hyperparameters: Hyperparameters | None = None,
) -> FittedModel:
    """Only internally validated, synthetic training partitions may fit.

    The permit is bound by the pipeline to the exact feature matrix and
    targets. Accepting caller-supplied X/y here would bypass that gate.
    """
    from app.modules.research.ml_pipeline import require_training_permit

    authorized = require_training_permit(prepared)
    y = (
        authorized.y_regression if task == "regression"
        else tuple(float(value) for value in authorized.y_classification)
    )
    return _fit_numeric(
        family, task, authorized.x, y, authorized.feature_names, seed,
        hyperparameters,
    )


def _fit_numeric(
    family: str,
    task: Task,
    X: tuple[tuple[float, ...], ...],
    y: tuple[float, ...],
    feature_names: tuple[str, ...],
    seed: int,
    hyperparameters: Hyperparameters | None = None,
) -> FittedModel:
    """Internal numeric fitter; public callers must use the permit gate."""
    if family not in FAMILIES or task not in ("regression", "classification"):
        raise ValueError("unsupported model family or task")
    if type(seed) is not int or seed < 0:
        raise ValueError("random seed must be a non-negative integer")
    _validate_X(X, feature_names)
    _validate_y(y, task)
    if (task == "classification" and family in (
        _NATIVE | _SKLEARN_TREES | {"logistic_regression"}
    ) and set(y) != {0.0, 1.0}):
        raise ValueError("classifier requires both training classes")
    if len(X) != len(y):
        raise ValueError("feature and target row counts differ")
    params = _validated_hyperparameters(hyperparameters)
    if family == "constant_return":
        if task != "regression" or params:
            raise ValueError("constant_return supports plain regression only")
        return FittedModel(
            family, task, feature_names, seed, params,
            {"mean_return": sum(y) / len(y)},
        )
    if family == "majority_class":
        if task != "classification" or params:
            raise ValueError("majority_class supports plain classification only")
        return FittedModel(
            family, task, feature_names, seed, params,
            {"prevalence": sum(y) / len(y)},
        )
    if family == "momentum":
        if set(params) - {"momentum_feature"}:
            raise ValueError("unsupported momentum hyperparameter")
        name = params.get("momentum_feature", "momentum")
        if not isinstance(name, str) or name not in feature_names:
            raise ValueError("momentum feature is absent")
        state: dict[str, Any] = {"feature_name": name}
        if task == "classification":
            index = feature_names.index(name)
            positive = [target for row, target in zip(X, y, strict=True)
                        if row[index] > 0]
            nonpositive = [target for row, target in zip(X, y, strict=True)
                           if row[index] <= 0]
            overall = sum(y) / len(y)
            state["positive_prevalence"] = (
                sum(positive) / len(positive) if positive else overall
            )
            state["nonpositive_prevalence"] = (
                sum(nonpositive) / len(nonpositive) if nonpositive else overall
            )
        return FittedModel(family, task, feature_names, seed, params, state)
    return _fit_optional(family, task, X, y, feature_names, seed, params)


def _fit_optional(
    family: str, task: Task, X: tuple[tuple[float, ...], ...],
    y: tuple[float, ...], feature_names: tuple[str, ...], seed: int,
    params: Hyperparameters,
) -> FittedModel:
    if family in _LINEAR:
        return _fit_linear(family, task, X, y, feature_names, seed, params)
    if family in _SKLEARN_TREES:
        return _fit_tree(family, task, X, y, feature_names, seed, params)
    package = _optional_import(family)
    numpy = _optional_import("numpy")
    from importlib.metadata import version

    library_version = version(family)
    rounds = params.get("num_boost_round", 20)
    if type(rounds) is not int or rounds < 1 or rounds > 10_000:
        raise ValueError("num_boost_round must be an integer in [1, 10000]")
    algorithm_params = {key: value for key, value in params.items()
                        if key != "num_boost_round"}
    matrix = numpy.asarray(X, dtype="float64")
    target = numpy.asarray(y, dtype="float64")
    if family == "lightgbm":
        forbidden = {"objective", "seed", "data_random_seed",
                     "feature_fraction_seed", "bagging_seed", "num_threads"}
        if forbidden & algorithm_params.keys():
            raise ValueError("objective, seed and deterministic settings are fixed")
        settings: dict[str, Any] = {
            **algorithm_params,
            "objective": "regression" if task == "regression" else "binary",
            "seed": seed, "data_random_seed": seed,
            "feature_fraction_seed": seed, "bagging_seed": seed,
            "num_threads": 1, "deterministic": True, "verbosity": -1,
        }
        dataset = package.Dataset(
            matrix, label=target, feature_name=list(feature_names),
            free_raw_data=False,
        )
        native = package.train(settings, dataset, num_boost_round=rounds)
    elif family == "xgboost":
        forbidden = {"objective", "seed", "nthread"}
        if forbidden & algorithm_params.keys():
            raise ValueError("objective, seed and deterministic settings are fixed")
        settings = {
            **algorithm_params,
            "objective": "reg:squarederror" if task == "regression"
            else "binary:logistic",
            "seed": seed, "nthread": 1,
        }
        dataset = package.DMatrix(
            matrix, label=target, feature_names=list(feature_names)
        )
        native = package.train(settings, dataset, num_boost_round=rounds)
    else:
        if set(algorithm_params) - {"depth", "learning_rate", "l2_leaf_reg"}:
            raise ValueError("unsupported CatBoost hyperparameter")
        settings = {
            **algorithm_params,
            "iterations": rounds, "random_seed": seed, "thread_count": 1,
            "allow_writing_files": False, "verbose": False,
            "loss_function": "RMSE" if task == "regression" else "Logloss",
        }
        estimator = (package.CatBoostRegressor if task == "regression"
                     else package.CatBoostClassifier)
        native = estimator(**settings)
        native.fit(matrix, target)
    return FittedModel(
        family, task, feature_names, seed, params, {}, library_version, native
    )


def _fit_linear(
    family: str, task: Task, X: tuple[tuple[float, ...], ...],
    y: tuple[float, ...], feature_names: tuple[str, ...], seed: int,
    params: Hyperparameters,
) -> FittedModel:
    if (family == "logistic_regression") != (task == "classification"):
        raise ValueError("linear model family and task are incompatible")
    allowed = {"C"} if family == "logistic_regression" else {"alpha"}
    if set(params) - allowed:
        raise ValueError("unsupported linear-model hyperparameter")
    for value in params.values():
        if not isinstance(value, (int, float)) or value <= 0:
            raise ValueError("linear-model regularization must be positive")
    if task == "classification" and set(y) != {0.0, 1.0}:
        raise ValueError("logistic regression requires both classes")
    linear = _optional_import("sklearn.linear_model")
    numpy = _optional_import("numpy")
    from importlib.metadata import version

    matrix = numpy.asarray(X, dtype="float64")
    target = numpy.asarray(y, dtype="float64")
    if family == "ridge":
        estimator = linear.Ridge(alpha=params.get("alpha", 1.0))
    elif family == "elastic_net":
        estimator = linear.ElasticNet(
            alpha=params.get("alpha", 0.01), random_state=seed,
            max_iter=1000, selection="cyclic",
        )
    else:
        estimator = linear.LogisticRegression(
            C=params.get("C", 1.0), random_state=seed, max_iter=1000,
        )
    estimator.fit(matrix, target)
    state = {
        "coefficients": [float(value) for value in estimator.coef_.reshape(-1)],
        "intercept": float(estimator.intercept_.reshape(-1)[0]),
    }
    _validate_linear_state(state, feature_names)
    return FittedModel(
        family, task, feature_names, seed, params, state,
        version("scikit-learn"),
    )


def _fit_tree(
    family: str, task: Task, X: tuple[tuple[float, ...], ...],
    y: tuple[float, ...], feature_names: tuple[str, ...], seed: int,
    params: Hyperparameters,
) -> FittedModel:
    if task == "classification" and set(y) != {0.0, 1.0}:
        raise ValueError("tree classification requires both classes")
    count_name = ("max_iter" if family == "hist_gradient_boosting"
                  else "n_estimators")
    if set(params) - {count_name, "max_depth"}:
        raise ValueError("unsupported tree-model hyperparameter")
    for name, value in params.items():
        limit = 32 if name == "max_depth" else 500
        if type(value) is not int or not 1 <= value <= limit:
            raise ValueError("tree-model counts must be bounded positive integers")
    ensemble = _optional_import("sklearn.ensemble")
    _optional_import("skops.io")
    numpy = _optional_import("numpy")
    from importlib.metadata import version

    classes = {
        "random_forest": (
            ensemble.RandomForestRegressor, ensemble.RandomForestClassifier,
        ),
        "extra_trees": (
            ensemble.ExtraTreesRegressor, ensemble.ExtraTreesClassifier,
        ),
        "hist_gradient_boosting": (
            ensemble.HistGradientBoostingRegressor,
            ensemble.HistGradientBoostingClassifier,
        ),
    }
    constructor = classes[family][task == "classification"]
    settings: dict[str, Any] = {
        count_name: params.get(count_name, 20), "random_state": seed,
    }
    if "max_depth" in params:
        settings["max_depth"] = params["max_depth"]
    if family == "hist_gradient_boosting":
        settings["early_stopping"] = False
    else:
        settings["n_jobs"] = 1
    model = constructor(**settings)
    model.fit(numpy.asarray(X, dtype="float64"), numpy.asarray(y))
    return FittedModel(
        family, task, feature_names, seed, params,
        {"skops_version": version("skops")},
        version("scikit-learn"), model,
    )


def load_model(payload: bytes, *, expected_sha256: str) -> FittedModel:
    """Load only checksum-verified artifacts produced by this module.

    The caller must obtain the expected digest from a trusted experiment
    manifest, not from the supplied file itself. A checksum detects edits;
    it is not a signature or independent proof of model provenance. Native
    model parsers are never invoked before the digest check.
    """
    if len(payload) > _MAX_ARTIFACT_BYTES:
        raise ValueError("model artifact exceeds 20 MB")
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ValueError("invalid expected model checksum")
    if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), expected_sha256):
        raise ValueError("model artifact checksum mismatch")
    try:
        raw = json.loads(payload.decode("utf-8"),
                         parse_constant=lambda _: (_ for _ in ()).throw(
                             ValueError("invalid JSON number")
                         ))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid model artifact JSON") from exc
    if not isinstance(raw, dict) or raw.get("format_version") != 1:
        raise ValueError("unsupported model artifact version")
    family, task = raw.get("family"), raw.get("task")
    if (
        not isinstance(family, str) or family not in FAMILIES
        or task not in ("regression", "classification")
    ):
        raise ValueError("unsupported model artifact family or task")
    names = raw.get("feature_names")
    if not isinstance(names, list) or any(not isinstance(x, str) for x in names):
        raise ValueError("invalid model artifact feature names")
    feature_names = tuple(names)
    _validate_X((), feature_names, allow_empty=True)
    seed = raw.get("seed")
    if type(seed) is not int or seed < 0:
        raise ValueError("invalid model artifact seed")
    if not isinstance(raw.get("hyperparameters"), dict):
        raise ValueError("invalid model artifact hyperparameters")
    parameters = _validated_hyperparameters(raw["hyperparameters"])
    state = raw.get("state")
    native = raw.get("native_model")
    library_version = raw.get("library_version")
    if library_version is not None and not isinstance(library_version, str):
        raise ValueError("invalid model library version")
    if not isinstance(state, dict):
        raise ValueError("invalid model artifact state")
    if family in ("constant_return", "majority_class", "momentum"):
        if native is not None:
            raise ValueError("baseline artifact cannot contain native model")
        _validate_baseline_state(family, task, state, feature_names)
        return FittedModel(
            family, task, feature_names, seed, parameters, state,
            library_version,
        )
    if family in _LINEAR:
        if native is not None:
            raise ValueError("linear artifact cannot contain native model")
        if (family == "logistic_regression") != (task == "classification"):
            raise ValueError("linear artifact task is incompatible")
        _validate_linear_state(state, feature_names)
        return FittedModel(
            family, task, feature_names, seed, parameters, state,
            library_version,
        )
    if not isinstance(native, str) or not native:
        raise ValueError("native model content is missing")
    from importlib.metadata import version

    if family in _SKLEARN_TREES:
        if library_version != version("scikit-learn"):
            raise ValueError("native model library version differs")
        if state != {"skops_version": version("skops")}:
            raise ValueError("skops artifact version differs")
        try:
            decoded = base64.b64decode(native, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid native model encoding") from exc
        sio = _optional_import("skops.io")
        unexpected = set(sio.get_untrusted_types(data=decoded)) - (
            _SKOPS_ALLOWED[family]
        )
        if unexpected:
            raise ValueError("skops artifact contains unexpected types")
        loaded = sio.loads(decoded, trusted=sorted(_SKOPS_ALLOWED[family]))
        ensemble = _optional_import("sklearn.ensemble")
        class_names = {
            "random_forest": ("RandomForestRegressor", "RandomForestClassifier"),
            "extra_trees": ("ExtraTreesRegressor", "ExtraTreesClassifier"),
            "hist_gradient_boosting": (
                "HistGradientBoostingRegressor", "HistGradientBoostingClassifier"
            ),
        }
        expected_type = getattr(ensemble, class_names[family][task == "classification"])
        if type(loaded) is not expected_type or loaded.n_features_in_ != len(
            feature_names
        ):
            raise ValueError("loaded tree model schema differs")
        if task == "classification" and tuple(loaded.classes_) != (0.0, 1.0):
            raise ValueError("loaded tree model classes differ")
        return FittedModel(
            family, task, feature_names, seed, parameters, state,
            library_version, loaded,
        )
    package = _optional_import(family)
    if library_version != version(family):
        raise ValueError("native model library version differs")
    if family == "lightgbm":
        loaded = package.Booster(model_str=native)
    else:
        loaded = (
            package.Booster() if family == "xgboost"
            else (package.CatBoostClassifier() if task == "classification"
                  else package.CatBoostRegressor())
        )
        try:
            decoded = base64.b64decode(native, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid native model encoding") from exc
        if family == "xgboost":
            loaded.load_model(bytearray(decoded))
        else:
            with tempfile.TemporaryDirectory(prefix="research-model-") as folder:
                path = Path(folder) / "model.json"
                path.write_bytes(decoded)
                loaded.load_model(str(path), format="json")
    return FittedModel(
        family, task, feature_names, seed, parameters, state,
        library_version, loaded,
    )


def _validate_linear_state(
    state: dict[str, Any], feature_names: tuple[str, ...],
) -> None:
    if set(state) != {"coefficients", "intercept"}:
        raise ValueError("invalid linear model state")
    coefficients = state["coefficients"]
    if not isinstance(coefficients, list) or len(coefficients) != len(feature_names):
        raise ValueError("invalid linear model coefficients")
    values = (*coefficients, state["intercept"])
    if any(type(value) not in (int, float) or not math.isfinite(value)
           for value in values):
        raise ValueError("invalid linear model value")


def _validate_baseline_state(
    family: str, task: Task, state: dict[str, Any],
    feature_names: tuple[str, ...],
) -> None:
    if family == "constant_return":
        if task != "regression" or set(state) != {"mean_return"}:
            raise ValueError("invalid constant-return artifact")
        values = (state["mean_return"],)
    elif family == "majority_class":
        if task != "classification" or set(state) != {"prevalence"}:
            raise ValueError("invalid majority-class artifact")
        values = (state["prevalence"],)
    else:
        expected = {"feature_name"}
        if task == "classification":
            expected |= {"positive_prevalence", "nonpositive_prevalence"}
        if set(state) != expected or state["feature_name"] not in feature_names:
            raise ValueError("invalid momentum artifact")
        values = tuple(state[key] for key in expected if key != "feature_name")
    if any(type(value) not in (int, float) or not math.isfinite(value)
           for value in values):
        raise ValueError("invalid baseline model value")
    if family != "constant_return" and task == "classification" and any(
        not 0 <= value <= 1 for value in values
    ):
        raise ValueError("invalid classifier probability")
