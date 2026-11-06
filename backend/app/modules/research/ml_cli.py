"""Synthetic-only ML research commands; never load app settings or a database."""

import argparse
import hashlib
import importlib.metadata
import json
import sys
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from app.core.config import PROJECT_ROOT
from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.ml_metrics import (
    evaluate_classification,
    evaluate_regression,
)
from app.modules.research.ml_models import (
    FAMILIES,
    MissingDependencyError,
    Task,
    fit_model,
    load_model,
)
from app.modules.research.ml_pipeline import (
    SCHEMA_VERSION,
    FittedPreprocessor,
    FixtureSpec,
    Preprocessor,
    ValidatedDataset,
    WalkForwardPlan,
    make_trusted_synthetic,
    prepare_training,
    validate_dataset,
    walk_forward_folds,
)
from app.modules.research.ml_registry import (
    ExperimentRegistryError,
    consume_holdout_once,
    list_experiments,
    load_experiment,
    save_experiment,
)

COMMANDS = frozenset({
    "research-ml-validate", "research-ml-train-synthetic",
    "research-ml-evaluate", "research-ml-list-experiments",
})
ML_EXPERIMENTS_ROOT = PROJECT_ROOT / "ml-experiments"
_BANNER = (
    "SYNTHETIC RESEARCH ONLY — metrics are not market predictions, trading "
    "signals, or evidence of profitability."
)


def _add_fixture_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--strategy", choices=tuple(item.value for item in StrategyFamily),
        default=StrategyFamily.SWING.value,
    )
    parser.add_argument(
        "--timeframe", choices=tuple(item.value for item in Timeframe),
        default=Timeframe.DAY.value,
    )
    parser.add_argument("--horizon", type=int, default=5)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--sessions", type=int)
    parser.add_argument("--instruments", type=int, default=2)
    parser.add_argument("--scenario", choices=("uptrend", "alternating"),
                        default="uptrend")
    parser.add_argument("--min-samples", type=int, default=30)


def _add_window_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--train-size", type=int)
    parser.add_argument("--validation-size", type=int)
    parser.add_argument("--test-size", type=int)
    parser.add_argument("--step-size", type=int)
    parser.add_argument("--mode", choices=("expanding", "rolling"),
                        default="expanding")
    parser.add_argument("--embargo-minutes", type=int, default=0)
    parser.add_argument("--holdout-size", type=int)
    parser.add_argument("--min-partition-samples", type=int, default=1)


def add_ml_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register bounded commands; none accept arbitrary training-data paths."""
    validate = subparsers.add_parser(
        "research-ml-validate", help="Validate trusted generated synthetic ML data"
    )
    _add_fixture_options(validate)
    _add_window_options(validate)

    train = subparsers.add_parser(
        "research-ml-train-synthetic",
        help="Train and record an offline model on trusted synthetic data",
    )
    _add_fixture_options(train)
    _add_window_options(train)
    train.add_argument("--model-family", choices=tuple(sorted(FAMILIES)),
                       default="constant_return")
    train.add_argument("--task", choices=("regression", "classification"),
                       default="regression")
    train.add_argument("--feature", action="append", dest="features")
    train.add_argument("--missing-policy", choices=("reject", "median"),
                       default="reject")
    train.add_argument("--scale", action="store_true")
    train.add_argument("--hyperparameters", default="{}",
                       help="JSON object of explicit scalar hyperparameters")
    train.add_argument("--verbose-folds", action="store_true",
                       help="Print every fold metric as well as the aggregate")

    evaluate = subparsers.add_parser(
        "research-ml-evaluate",
        help="Reload an isolated experiment and recompute synthetic metrics",
    )
    evaluate.add_argument("experiment_id")
    evaluate.add_argument("--partition", choices=("test", "holdout"),
                          default="test")
    evaluate.add_argument(
        "--unlock-holdout", action="store_true",
        help="Consume the one-time final holdout evaluation after model freeze",
    )

    subparsers.add_parser(
        "research-ml-list-experiments",
        help="List locally saved synthetic ML experiment IDs",
    )


def _fixture_spec(arguments: argparse.Namespace) -> FixtureSpec:
    return FixtureSpec(
        strategy=StrategyFamily(arguments.strategy),
        timeframe=Timeframe(arguments.timeframe),
        horizon=arguments.horizon, seed=arguments.seed,
        sessions=arguments.sessions, instruments=arguments.instruments,
        scenario=arguments.scenario,
    )


def _validated(arguments: argparse.Namespace) -> ValidatedDataset:
    spec = _fixture_spec(arguments)
    return validate_dataset(
        make_trusted_synthetic(spec), spec, min_samples=arguments.min_samples
    )


def _window_config(
    arguments: argparse.Namespace, spec: FixtureSpec,
) -> dict[str, object]:
    if spec.strategy is StrategyFamily.SWING:
        default_train, default_validation, default_test = (
            (20, 12, 8) if spec.horizon == 5 else (30, 20, 8)
        )
    elif spec.strategy is StrategyFamily.LONG_TERM:
        default_train, default_validation, default_test = (
            (90, 80, 15) if spec.horizon == 60 else (150, 140, 20)
        )
    else:
        horizon_bars = spec.horizon // spec.timeframe.minutes
        default_validation = horizon_bars + 2
        default_train = default_validation + horizon_bars + 1
        default_test = 4 if spec.timeframe is Timeframe.MINUTE_15 else 8
    default_step = default_test
    if spec.strategy is StrategyFamily.INTRADAY:
        valid_bars_per_session = (90 - spec.horizon) // spec.timeframe.minutes
        default_step = max(
            default_test, spec.session_count * valid_bars_per_session // 6
        )
    test_size = (
        arguments.test_size if arguments.test_size is not None else default_test
    )
    return {
        "train_size": (
            arguments.train_size if arguments.train_size is not None
            else default_train
        ),
        "validation_size": (
            arguments.validation_size if arguments.validation_size is not None
            else default_validation
        ),
        "test_size": test_size,
        "step_size": (
            arguments.step_size if arguments.step_size is not None
            else max(default_step, test_size)
        ),
        "mode": arguments.mode,
        "embargo_minutes": arguments.embargo_minutes,
        "holdout_size": (
            arguments.holdout_size if arguments.holdout_size is not None
            else test_size
        ),
        "min_partition_samples": arguments.min_partition_samples,
    }


def _plan(dataset: ValidatedDataset, values: dict[str, object]) -> WalkForwardPlan:
    integers = (
        "train_size", "validation_size", "test_size", "step_size",
        "embargo_minutes", "holdout_size", "min_partition_samples",
    )
    if any(type(values.get(name)) is not int for name in integers):
        raise ValueError("invalid saved walk-forward configuration")
    if values.get("mode") not in ("expanding", "rolling"):
        raise ValueError("invalid saved walk-forward mode")
    parsed = cast(dict[str, int | str], values)
    if cast(int, parsed["step_size"]) < cast(int, parsed["test_size"]):
        raise ValueError("step size must cover the test window")
    mode = cast(Literal["expanding", "rolling"], parsed["mode"])
    return walk_forward_folds(
        dataset,
        train_size=cast(int, parsed["train_size"]),
        validation_size=cast(int, parsed["validation_size"]),
        test_size=cast(int, parsed["test_size"]),
        step_size=cast(int, parsed["step_size"]),
        mode=mode,
        embargo=timedelta(minutes=cast(int, parsed["embargo_minutes"])),
        holdout_size=cast(int, parsed["holdout_size"]),
        min_partition_samples=cast(int, parsed["min_partition_samples"]),
    )


def _spec_record(spec: FixtureSpec) -> dict[str, object]:
    record: dict[str, object] = {
        "strategy": spec.strategy.value, "timeframe": spec.timeframe.value,
        "horizon": spec.horizon, "seed": spec.seed,
        "sessions": spec.sessions, "instruments": spec.instruments,
    }
    if spec.scenario != "uptrend":
        record["scenario"] = spec.scenario
    return record


def _selected_features(arguments: argparse.Namespace, dataset: ValidatedDataset,
                       family: str) -> tuple[str, ...]:
    default = (
        "session_vwap_paise" if dataset.spec.strategy is StrategyFamily.INTRADAY
        else "daily_return"
    )
    selected = tuple(arguments.features or (default,))
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("feature names must be unique and nonempty")
    if any(name not in dataset.feature_names for name in selected):
        raise ValueError("selected feature is absent from dataset schema")
    if family == "momentum" and "momentum" not in selected:
        raise ValueError("momentum model requires the momentum feature")
    return selected


def _hyperparameters(raw: str) -> dict[str, int | float | str]:
    value = json.loads(raw)
    if not isinstance(value, dict) or any(
        not isinstance(name, str) or type(item) not in (int, float, str)
        for name, item in value.items()
    ):
        raise ValueError("hyperparameters must be a JSON object of scalars")
    return cast(dict[str, int | float | str], value)


def _metrics(task: Task, rows: tuple[Any, ...], predictions: tuple[float, ...]
             ) -> dict[str, float | None]:
    if task == "regression":
        truth = tuple(float(row.target_return) for row in rows)
        return evaluate_regression(truth, predictions)
    truth = tuple(float(row.positive_return_label) for row in rows)
    return evaluate_classification(truth, predictions)


def _packages() -> dict[str, str | None]:
    result: dict[str, str | None] = {
        "python": sys.version.split()[0],
    }
    for name in ("lightgbm", "xgboost", "catboost", "scikit-learn", "skops",
                 "numpy", "scipy"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def _run_validate(arguments: argparse.Namespace) -> int:
    dataset = _validated(arguments)
    plan = _plan(dataset, _window_config(arguments, dataset.spec))
    print(_BANNER)
    print(
        f"Validated {len(dataset.rows)} synthetic samples; "
        f"fingerprint {dataset.fingerprint}; folds {len(plan.folds)}; "
        f"holdout {len(plan.holdout.rows) if plan.holdout else 0}"
    )
    return 0


def _run_train(arguments: argparse.Namespace) -> int:
    dataset = _validated(arguments)
    windows = _window_config(arguments, dataset.spec)
    plan = _plan(dataset, windows)
    family, task = arguments.model_family, cast(Task, arguments.task)
    features = _selected_features(arguments, dataset, family)
    parameters = _hyperparameters(arguments.hyperparameters)
    baseline_family = (
        "constant_return" if task == "regression" else "majority_class"
    )
    fold_records: list[dict[str, object]] = []
    all_test_rows: list[Any] = []
    all_test_predictions: list[float] = []
    all_baseline_predictions: list[float] = []
    final_model = None
    final_preprocessor = None
    for fold in plan.folds:
        fitted = Preprocessor(
            feature_names=features, missing_policy=arguments.missing_policy,
            scale=arguments.scale,
        ).fit(fold.train)
        prepared = prepare_training(fold.train, fitted)
        model = fit_model(family, task, prepared, dataset.spec.seed, parameters)
        baseline = fit_model(
            baseline_family, task, prepared, dataset.spec.seed
        )
        validation_x = fitted.transform(fold.validation)
        test_x = fitted.transform(fold.test)
        test_predictions = model.predict(test_x)
        baseline_predictions = baseline.predict(test_x)
        all_test_rows.extend(fold.test.rows)
        all_test_predictions.extend(test_predictions)
        all_baseline_predictions.extend(baseline_predictions)
        fold_records.append({
            "index": fold.index,
            "train_start_utc": fold.train_start_utc.isoformat(),
            "train_end_utc": fold.train_end_utc.isoformat(),
            "validation_end_utc": fold.validation_end_utc.isoformat(),
            "test_end_utc": fold.test_end_utc.isoformat(),
            "train_rows": len(fold.train.rows),
            "validation_rows": len(fold.validation.rows),
            "test_rows": len(fold.test.rows),
            "excluded_rows": fold.excluded_count,
            "validation_metrics": _metrics(
                task, fold.validation.rows, model.predict(validation_x)
            ),
            "test_metrics": _metrics(
                task, fold.test.rows, test_predictions
            ),
            "baseline_family": baseline_family,
            "baseline_test_metrics": _metrics(
                task, fold.test.rows, baseline_predictions
            ),
        })
        final_model, final_preprocessor = model, fitted
    assert final_model is not None and final_preprocessor is not None
    aggregate_metrics = _metrics(
        task, tuple(all_test_rows), tuple(all_test_predictions)
    )
    aggregate_baseline = _metrics(
        task, tuple(all_test_rows), tuple(all_baseline_predictions)
    )
    model_bytes = final_model.serialize()
    key: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "dataset_fingerprint": dataset.fingerprint,
        "fixture_spec": _spec_record(dataset.spec),
        "model_family": family, "task": task,
        "hyperparameters": parameters, "features": features,
        "missing_policy": arguments.missing_policy,
        "scale": arguments.scale,
        "walk_forward": windows,
        "min_samples": arguments.min_samples,
    }
    metadata: dict[str, object] = {
        "synthetic": True,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "dataset_schema_version": dataset.schema_version,
        "min_samples": arguments.min_samples,
        "dataset_fingerprint": dataset.fingerprint,
        "fixture_provenance": dataset.fixture_provenance,
        "fixture_spec": _spec_record(dataset.spec),
        "model_family": family, "task": task,
        "hyperparameters": parameters, "random_seed": dataset.spec.seed,
        "feature_names": features,
        "walk_forward_configuration": windows,
        "walk_forward_folds": fold_records,
        "aggregate_test_metrics": aggregate_metrics,
        "aggregate_baseline_metrics": aggregate_baseline,
        "holdout_rows": len(plan.holdout.rows) if plan.holdout else 0,
        "fitted_preprocessing": final_preprocessor.to_dict(),
        "model_artifact_sha256": hashlib.sha256(model_bytes).hexdigest(),
        "dependency_versions": _packages(),
        "warnings": [
            "Synthetic outcomes are not evidence of predictive accuracy or profit",
            "Final holdout was not used for model selection",
        ],
    }
    experiment_id = save_experiment(
        ML_EXPERIMENTS_ROOT, key, metadata, model_bytes
    )
    print(_BANNER)
    print(
        f"Experiment {experiment_id}; {dataset.spec.strategy.value}/"
        f"{dataset.spec.timeframe.value} horizon={dataset.spec.horizon}; "
        f"{family} {task}; folds={len(plan.folds)}; "
        f"holdout unevaluated={metadata['holdout_rows']}"
    )
    if arguments.verbose_folds:
        for fold_record in fold_records:
            print(
                f"Fold {fold_record['index']}: train={fold_record['train_rows']} "
                f"validation={fold_record['validation_rows']} "
                f"test={fold_record['test_rows']} "
                f"metrics={json.dumps(fold_record['test_metrics'], sort_keys=True)}"
            )
    print(
        "Aggregate out-of-sample metrics="
        f"{json.dumps(aggregate_metrics, sort_keys=True)}; "
        f"baseline={json.dumps(aggregate_baseline, sort_keys=True)}"
    )
    return 0


def _get_object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(
        not isinstance(key, str) for key in value
    ):
        raise ValueError(f"invalid saved {name}")
    return cast(dict[str, object], value)


def _get_int(value: object, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"invalid saved {name}")
    return cast(int, value)


def _get_str(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"invalid saved {name}")
    return value


def _run_evaluate(arguments: argparse.Namespace) -> int:
    if arguments.partition == "holdout" and not arguments.unlock_holdout:
        raise ValueError("final holdout requires --unlock-holdout")
    if arguments.partition != "holdout" and arguments.unlock_holdout:
        raise ValueError("--unlock-holdout is only valid for final holdout")
    metadata, artifact = load_experiment(
        ML_EXPERIMENTS_ROOT, arguments.experiment_id
    )
    if metadata.get("synthetic") is not True or artifact is None:
        raise ValueError("only complete synthetic experiments can be evaluated")
    if metadata.get("dataset_schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported saved dataset schema")
    saved_spec = _get_object(metadata.get("fixture_spec"), "fixture spec")
    spec = FixtureSpec(
        strategy=StrategyFamily(_get_str(saved_spec.get("strategy"), "strategy")),
        timeframe=Timeframe(_get_str(saved_spec.get("timeframe"), "timeframe")),
        horizon=_get_int(saved_spec.get("horizon"), "horizon"),
        seed=_get_int(saved_spec.get("seed"), "seed"),
        sessions=(None if saved_spec.get("sessions") is None else _get_int(
            saved_spec.get("sessions"), "sessions"
        )),
        instruments=_get_int(saved_spec.get("instruments"), "instruments"),
        scenario=cast(Literal["uptrend", "alternating"], _get_str(
            saved_spec.get("scenario", "uptrend"), "scenario"
        )),
    )
    dataset = validate_dataset(
        make_trusted_synthetic(spec), spec,
        min_samples=_get_int(metadata.get("min_samples"), "sample minimum"),
    )
    if dataset.fingerprint != metadata.get("dataset_fingerprint"):
        raise ValueError("saved dataset fingerprint does not match fixture")
    windows = _get_object(
        metadata.get("walk_forward_configuration"), "walk-forward settings"
    )
    plan = _plan(dataset, windows)
    final_fold = plan.folds[-1]
    saved_fitted = _get_object(
        metadata.get("fitted_preprocessing"), "preprocessor"
    )
    fitted = FittedPreprocessor.from_dict(saved_fitted, dataset.fingerprint)
    expected_fit = Preprocessor(
        fitted.feature_names, fitted.missing_policy, fitted.scale
    ).fit(final_fold.train)
    if expected_fit.to_dict() != fitted.to_dict():
        raise ValueError("saved preprocessing differs from training-only fit")
    expected_hash = _get_str(
        metadata.get("model_artifact_sha256"), "artifact digest"
    )
    model = load_model(artifact, expected_sha256=expected_hash)
    if (
        model.feature_names != fitted.feature_names
        or model.family != metadata.get("model_family")
        or model.task != metadata.get("task")
        or model.seed != spec.seed
    ):
        raise ValueError("saved model metadata differs from validated experiment")
    partition = (
        final_fold.test if arguments.partition == "test" else plan.holdout
    )
    if partition is None or not partition.rows:
        raise ValueError("requested evaluation partition is empty")
    if arguments.partition == "holdout":
        consume_holdout_once(ML_EXPERIMENTS_ROOT, arguments.experiment_id)
    result = _metrics(model.task, partition.rows,
                      model.predict(fitted.transform(partition)))
    if arguments.partition == "test":
        stored_folds = metadata.get("walk_forward_folds")
        if not isinstance(stored_folds, list) or not stored_folds:
            raise ValueError("saved fold metrics missing")
        final_saved = _get_object(stored_folds[-1], "final fold")
        if result != final_saved.get("test_metrics"):
            raise ValueError("recomputed test metrics differ from saved metrics")
    print(_BANNER)
    print(
        f"Experiment {arguments.experiment_id}; partition={arguments.partition}; "
        f"samples={len(partition.rows)}; "
        f"metrics={json.dumps(result, sort_keys=True)}"
    )
    return 0


def run_ml_cli(arguments: argparse.Namespace) -> int:
    """Dispatch without application settings, credentials, data, or network."""
    try:
        if arguments.command == "research-ml-validate":
            return _run_validate(arguments)
        if arguments.command == "research-ml-train-synthetic":
            return _run_train(arguments)
        if arguments.command == "research-ml-evaluate":
            return _run_evaluate(arguments)
        if arguments.command == "research-ml-list-experiments":
            identifiers = list_experiments(ML_EXPERIMENTS_ROOT)
            print(f"Synthetic ML experiments: {len(identifiers)}")
            for experiment_id in identifiers:
                print(experiment_id)
            return 0
        raise ValueError("unknown ML research command")
    except (ValueError, OSError, MissingDependencyError,
            ExperimentRegistryError) as exc:
        print(f"ML research error: {exc}", file=sys.stderr)
        return 1
