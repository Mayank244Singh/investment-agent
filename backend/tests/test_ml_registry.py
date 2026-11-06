"""Experiment records remain isolated, private, atomic and checksum-checked."""

import json
import os
from pathlib import Path

import pytest

from app.modules.research import ml_registry
from app.modules.research.ml_registry import (
    ExperimentRegistryError,
    consume_holdout_once,
    list_experiments,
    load_experiment,
    save_experiment,
)


def test_deterministic_id_private_permissions_and_round_trip(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    key: dict[str, object] = {
        "strategy": "swing", "dataset_fingerprint": "invented",
        "hyperparameters": {"depth": 2, "seed": 7},
    }
    metadata: dict[str, object] = {
        "synthetic": True, "metrics": {"mae": 0.2},
    }
    experiment_id = save_experiment(first, key, metadata, b"safe-model-bytes")
    assert experiment_id == save_experiment(
        second, dict(reversed(list(key.items()))), metadata, b"safe-model-bytes"
    )
    assert len(experiment_id) == 64
    assert load_experiment(first, experiment_id) == (metadata, b"safe-model-bytes")
    assert list_experiments(first) == (experiment_id,)
    assert first.stat().st_mode & 0o777 == 0o700
    assert (first / experiment_id).stat().st_mode & 0o777 == 0o700
    for filename in ("key.json", "metadata.json", "manifest.json", "model.bin"):
        assert (first / experiment_id / filename).stat().st_mode & 0o777 == 0o600


def test_duplicate_experiment_never_overwrites(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    key: dict[str, object] = {"dataset": "invented"}
    original = {"synthetic": True, "model": "constant"}
    experiment_id = save_experiment(root, key, original)
    with pytest.raises(ExperimentRegistryError, match="already exists"):
        save_experiment(root, key, {"synthetic": True, "model": "changed"})
    assert load_experiment(root, experiment_id) == (original, None)


def test_failed_final_rename_removes_staging_and_keeps_no_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "registry"

    def fail_rename(_source: Path, _target: Path) -> None:
        raise OSError("injected rename failure")

    monkeypatch.setattr(ml_registry.os, "rename", fail_rename)
    with pytest.raises(OSError, match="injected"):
        save_experiment(root, {"dataset": "invented"}, {"synthetic": True})
    assert list_experiments(root) == ()
    assert not list(root.glob(".staging-*"))


def test_checksum_tampering_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    experiment_id = save_experiment(
        root, {"dataset": "invented"}, {"synthetic": True}, b"abc"
    )
    artifact = root / experiment_id / "model.bin"
    artifact.write_bytes(b"abd")
    with pytest.raises(ExperimentRegistryError, match="checksum mismatch"):
        load_experiment(root, experiment_id)
    with pytest.raises(ExperimentRegistryError, match="checksum mismatch"):
        list_experiments(root)
    artifact.write_bytes(b"abc")
    metadata = root / experiment_id / "metadata.json"
    metadata.write_text(json.dumps({"synthetic": False}), encoding="utf-8")
    with pytest.raises(ExperimentRegistryError, match="checksum mismatch"):
        load_experiment(root, experiment_id)


def test_traversal_and_symlinks_are_refused(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    experiment_id = save_experiment(
        root, {"dataset": "invented"}, {"synthetic": True}
    )
    for bad_id in ("../outside", "a" * 63, "A" * 64, experiment_id + "/x"):
        with pytest.raises(ExperimentRegistryError, match="invalid experiment ID"):
            load_experiment(root, bad_id)
    symlink_root = tmp_path / "linked-registry"
    symlink_root.symlink_to(root, target_is_directory=True)
    with pytest.raises(ExperimentRegistryError, match="symlink"):
        list_experiments(symlink_root)
    metadata = root / experiment_id / "metadata.json"
    metadata.unlink()
    metadata.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ExperimentRegistryError, match="corrupt"):
        load_experiment(root, experiment_id)


def test_sensitive_and_non_json_payloads_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    bad_metadata: tuple[dict[str, object], ...] = (
        {"nested": {"UPSTOX_ANALYTICS_TOKEN": "forbidden"}},
        {"authorization": "forbidden"},
        {"account_id": 1},
        {"raw_response": "forbidden"},
    )
    for metadata in bad_metadata:
        with pytest.raises(ExperimentRegistryError, match="sensitive"):
            save_experiment(root, {"dataset": "invented"}, metadata)
    with pytest.raises(ExperimentRegistryError, match="JSON-native"):
        save_experiment(root, {"dataset": object()}, {"synthetic": True})
    with pytest.raises(ExperimentRegistryError, match="non-finite"):
        save_experiment(root, {"seed": float("nan")}, {"synthetic": True})
    assert not root.exists()


def test_artifact_limit_and_extra_files_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ml_registry, "MAX_ARTIFACT_BYTES", 2)
    root = tmp_path / "registry"
    with pytest.raises(ExperimentRegistryError, match="bounded bytes"):
        save_experiment(root, {"dataset": "invented"}, {"synthetic": True}, b"abc")
    experiment_id = save_experiment(
        root, {"dataset": "invented"}, {"synthetic": True}, b"ab"
    )
    (root / experiment_id / "unexpected.bin").write_bytes(os.urandom(1))
    with pytest.raises(ExperimentRegistryError, match="unexpected registry files"):
        load_experiment(root, experiment_id)


def test_final_holdout_seal_is_private_atomic_and_one_time(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    identifier = save_experiment(
        root, {"fixture": "invented"}, {"synthetic": True}, b"model"
    )
    consume_holdout_once(root, identifier)
    seal = root / ".holdout-seals" / identifier
    assert seal.is_file()
    assert seal.stat().st_mode & 0o777 == 0o600
    assert seal.parent.stat().st_mode & 0o777 == 0o700
    with pytest.raises(ExperimentRegistryError, match="already evaluated"):
        consume_holdout_once(root, identifier)
    assert list_experiments(root) == (identifier,)


def test_holdout_seal_refuses_symlink_directory(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    identifier = save_experiment(root, {"fixture": "invented"},
                                 {"synthetic": True})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / ".holdout-seals").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ExperimentRegistryError, match="unsafe"):
        consume_holdout_once(root, identifier)
    assert not list(elsewhere.iterdir())
