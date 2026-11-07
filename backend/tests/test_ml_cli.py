"""Synthetic-only ML CLI orchestration with an isolated temporary registry."""

import json
import socket
from pathlib import Path
from typing import NoReturn

import pytest

from app.cli import main
from app.modules.research import ml_cli, ml_models
from app.modules.research.ml_registry import list_experiments, load_experiment


def _forbid(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("ML CLI accessed network, settings or production DB")


def test_train_reload_evaluate_holdout_list_and_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = tmp_path / "ml-experiments"
    monkeypatch.setattr(ml_cli, "ML_EXPERIMENTS_ROOT", registry)
    monkeypatch.setattr("app.cli.get_settings", _forbid)
    monkeypatch.setattr("app.cli.create_engine", _forbid)
    monkeypatch.setattr(socket.socket, "connect", _forbid)
    command = ["research-ml-train-synthetic", "--seed", "7"]
    assert main(command) == 0
    output = capsys.readouterr().out
    assert "SYNTHETIC RESEARCH ONLY" in output
    assert "Aggregate out-of-sample metrics=" in output
    identifiers = list_experiments(registry)
    assert len(identifiers) == 1
    metadata, model_bytes = load_experiment(registry, identifiers[0])
    assert metadata["synthetic"] is True
    assert metadata["holdout_rows"] == 16
    folds = metadata["walk_forward_folds"]
    assert isinstance(folds, list) and len(folds) >= 2
    assert "aggregate_baseline_metrics" in metadata
    assert "aggregate_test_metrics" in metadata
    assert model_bytes is not None
    assert main(["research-ml-evaluate", identifiers[0]]) == 0
    assert "partition=test" in capsys.readouterr().out
    assert main([
        "research-ml-evaluate", identifiers[0], "--partition", "holdout"
    ]) == 1
    assert "requires --unlock-holdout" in capsys.readouterr().err
    assert main([
        "research-ml-evaluate", identifiers[0], "--partition", "holdout",
        "--unlock-holdout",
    ]) == 0
    assert "partition=holdout" in capsys.readouterr().out
    assert main([
        "research-ml-evaluate", identifiers[0], "--partition", "holdout",
        "--unlock-holdout",
    ]) == 1
    assert "already evaluated" in capsys.readouterr().err
    assert main(["research-ml-list-experiments"]) == 0
    assert identifiers[0] in capsys.readouterr().out
    assert main(command) == 1
    assert "already exists" in capsys.readouterr().err
    assert list_experiments(registry) == identifiers


def test_classification_and_momentum_baselines_are_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = tmp_path / "ml-experiments"
    monkeypatch.setattr(ml_cli, "ML_EXPERIMENTS_ROOT", registry)
    assert main([
        "research-ml-train-synthetic", "--task", "classification",
        "--model-family", "majority_class",
    ]) == 0
    assert main([
        "research-ml-train-synthetic", "--model-family", "momentum",
        "--feature", "momentum", "--missing-policy", "median",
    ]) == 0
    records = [load_experiment(registry, identifier)[0]
               for identifier in list_experiments(registry)]
    assert {record["model_family"] for record in records} == {
        "majority_class", "momentum"
    }
    for record in records:
        folds = record["walk_forward_folds"]
        assert isinstance(folds, list) and folds
        assert "aggregate_baseline_metrics" in record


@pytest.mark.parametrize("arguments", [
    ["--strategy", "swing", "--timeframe", "1d", "--horizon", "10"],
    ["--strategy", "long_term", "--timeframe", "1d", "--horizon", "60"],
    ["--strategy", "long_term", "--timeframe", "1d", "--horizon", "120"],
    ["--strategy", "intraday", "--timeframe", "1m", "--horizon", "60"],
    ["--strategy", "intraday", "--timeframe", "5m", "--horizon", "60"],
    ["--strategy", "intraday", "--timeframe", "15m", "--horizon", "60"],
])
def test_validate_all_strategy_timeframe_defaults(
    arguments: list[str], capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["research-ml-validate", *arguments]) == 0
    output = capsys.readouterr().out
    assert "Validated " in output
    assert "folds " in output
    assert "SYNTHETIC RESEARCH ONLY" in output


def test_real_data_path_and_target_feature_cannot_bypass_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = tmp_path / "ml-experiments"
    monkeypatch.setattr(ml_cli, "ML_EXPERIMENTS_ROOT", registry)
    with pytest.raises(SystemExit):
        main(["research-ml-train-synthetic", "--database", "/tmp/real.db"])
    assert main([
        "research-ml-train-synthetic", "--feature", "target_return"
    ]) == 1
    assert "selected feature is absent" in capsys.readouterr().err
    assert not registry.exists()


def test_optional_model_missing_dependency_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = tmp_path / "ml-experiments"
    monkeypatch.setattr(ml_cli, "ML_EXPERIMENTS_ROOT", registry)

    def unavailable(_name: str) -> NoReturn:
        raise ml_models.MissingDependencyError("optional ML package missing")

    monkeypatch.setattr(ml_models, "_optional_import", unavailable)
    assert main([
        "research-ml-train-synthetic", "--model-family", "lightgbm"
    ]) == 1
    assert "optional ML package missing" in capsys.readouterr().err
    assert not registry.exists()


def test_invalid_config_and_saved_artifact_tamper_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    registry = tmp_path / "ml-experiments"
    monkeypatch.setattr(ml_cli, "ML_EXPERIMENTS_ROOT", registry)
    assert main([
        "research-ml-validate", "--strategy", "intraday",
        "--timeframe", "1d",
    ]) == 1
    assert "incompatible intraday" in capsys.readouterr().err
    assert main(["research-ml-train-synthetic", "--step-size", "1"]) == 1
    assert "step size must cover" in capsys.readouterr().err
    assert main(["research-ml-train-synthetic", "--hyperparameters", "[]"]) == 1
    assert "hyperparameters must" in capsys.readouterr().err
    assert main(["research-ml-train-synthetic"]) == 0
    identifier = list_experiments(registry)[0]
    metadata_file = registry / identifier / "metadata.json"
    metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
    metadata["synthetic"] = False
    metadata_file.write_text(json.dumps(metadata), encoding="utf-8")
    assert main(["research-ml-evaluate", identifier]) == 1
    assert "checksum mismatch" in capsys.readouterr().err
