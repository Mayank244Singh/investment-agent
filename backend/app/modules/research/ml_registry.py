"""Private, atomic registry for synthetic-only offline ML experiment records.

The caller supplies already validated synthetic metadata. No pickle or code
deserialization is supported; optional model artifacts are bounded raw bytes.
"""

import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path

MAX_JSON_BYTES = 1_000_000
MAX_ARTIFACT_BYTES = 32_000_000
_ID = re.compile(r"[0-9a-f]{64}\Z")
_SENSITIVE_KEYS = (
    "token", "secret", "password", "credential", "authorization",
    "account", "cookie", "apikey", "rawresponse", "header",
)


class ExperimentRegistryError(ValueError):
    """Registry input, duplicate, path, or integrity failure."""


def _validate_json(value: object) -> None:
    if value is None or type(value) in (str, int, bool):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ExperimentRegistryError("non-finite registry value")
        return
    if isinstance(value, (tuple, list)):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ExperimentRegistryError("registry object keys must be strings")
            normalized = "".join(char for char in key.lower() if char.isalnum())
            if any(sensitive in normalized for sensitive in _SENSITIVE_KEYS):
                raise ExperimentRegistryError("sensitive registry field is forbidden")
            _validate_json(item)
        return
    raise ExperimentRegistryError("registry values must be JSON-native")


def _canonical(value: dict[str, object]) -> bytes:
    _validate_json(value)
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ExperimentRegistryError("invalid registry JSON") from exc
    if len(encoded) > MAX_JSON_BYTES:
        raise ExperimentRegistryError("registry JSON exceeds 1 MB")
    return encoded


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _checked_root(root: Path, *, create: bool) -> Path:
    if not root.is_absolute() or ".." in root.parts:
        raise ExperimentRegistryError("registry root must be an absolute safe path")
    if root.absolute() != root.resolve(strict=False):
        raise ExperimentRegistryError("registry path cannot contain a symlink")
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.exists():
        mode = root.lstat().st_mode
        if not stat.S_ISDIR(mode) or mode & 0o077:
            raise ExperimentRegistryError("registry root must be a private directory")
    return root


def _write_private(path: Path, content: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def _read_private(path: Path, maximum: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        information = os.fstat(descriptor)
        if not stat.S_ISREG(information.st_mode) or information.st_size > maximum:
            raise ExperimentRegistryError("invalid or oversized registry file")
        with os.fdopen(descriptor, "rb", closefd=False) as input_file:
            content = input_file.read(maximum + 1)
        if len(content) > maximum:
            raise ExperimentRegistryError("oversized registry file")
        return content
    finally:
        os.close(descriptor)


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _checked_id(experiment_id: str) -> None:
    if _ID.fullmatch(experiment_id) is None:
        raise ExperimentRegistryError("invalid experiment ID")


def save_experiment(
    root: Path,
    key: dict[str, object],
    metadata: dict[str, object],
    artifact: bytes | None = None,
) -> str:
    """Persist one unique record; a repeated key is refused, never overwritten."""
    key_bytes, metadata_bytes = _canonical(key), _canonical(metadata)
    if artifact is not None and (
        type(artifact) is not bytes or len(artifact) > MAX_ARTIFACT_BYTES
    ):
        raise ExperimentRegistryError("model artifact must be bounded bytes")
    experiment_id = _sha(key_bytes)
    checked_root = _checked_root(root, create=True)
    lock_path = checked_root / ".registry.lock"
    lock_fd = os.open(
        lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
            raise ExperimentRegistryError("invalid registry lock")
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        destination = checked_root / experiment_id
        if os.path.lexists(destination):
            raise ExperimentRegistryError("experiment already exists")
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=checked_root))
        try:
            manifest: dict[str, object] = {
                "schema_version": 1,
                "experiment_id": experiment_id,
                "key_sha256": experiment_id,
                "metadata_sha256": _sha(metadata_bytes),
                "artifact_sha256": _sha(artifact) if artifact is not None else None,
                "artifact_size": len(artifact) if artifact is not None else None,
            }
            _write_private(staging / "key.json", key_bytes)
            _write_private(staging / "metadata.json", metadata_bytes)
            if artifact is not None:
                _write_private(staging / "model.bin", artifact)
            _write_private(staging / "manifest.json", _canonical(manifest))
            _sync_directory(staging)
            if os.path.lexists(destination):
                raise ExperimentRegistryError("experiment already exists")
            os.rename(staging, destination)
            _sync_directory(checked_root)
        except Exception:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        return experiment_id
    finally:
        os.close(lock_fd)


def load_experiment(
    root: Path, experiment_id: str,
) -> tuple[dict[str, object], bytes | None]:
    """Load only checked JSON and bounded bytes after checksum verification."""
    _checked_id(experiment_id)
    checked_root = _checked_root(root, create=False)
    directory = checked_root / experiment_id
    try:
        if not stat.S_ISDIR(directory.lstat().st_mode):
            raise ExperimentRegistryError("experiment directory is not regular")
        names = {entry.name for entry in directory.iterdir()}
        if names not in (
            {"key.json", "metadata.json", "manifest.json"},
            {"key.json", "metadata.json", "manifest.json", "model.bin"},
        ):
            raise ExperimentRegistryError("unexpected registry files")
        manifest_bytes = _read_private(directory / "manifest.json", MAX_JSON_BYTES)
        manifest = json.loads(manifest_bytes)
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
            raise ExperimentRegistryError("unsupported registry manifest")
        key_bytes = _read_private(directory / "key.json", MAX_JSON_BYTES)
        metadata_bytes = _read_private(directory / "metadata.json", MAX_JSON_BYTES)
        if (
            manifest.get("experiment_id") != experiment_id
            or manifest.get("key_sha256") != experiment_id
            or _sha(key_bytes) != experiment_id
            or manifest.get("metadata_sha256") != _sha(metadata_bytes)
        ):
            raise ExperimentRegistryError("registry checksum mismatch")
        key = json.loads(key_bytes)
        metadata = json.loads(metadata_bytes)
        if not isinstance(key, dict) or not isinstance(metadata, dict):
            raise ExperimentRegistryError("invalid registry payload")
        if _canonical(key) != key_bytes or _canonical(metadata) != metadata_bytes:
            raise ExperimentRegistryError("noncanonical registry payload")
        artifact: bytes | None = None
        if "model.bin" in names:
            artifact = _read_private(directory / "model.bin", MAX_ARTIFACT_BYTES)
            if (
                manifest.get("artifact_sha256") != _sha(artifact)
                or manifest.get("artifact_size") != len(artifact)
            ):
                raise ExperimentRegistryError("model artifact checksum mismatch")
        elif manifest.get("artifact_sha256") is not None or (
            manifest.get("artifact_size") is not None
        ):
            raise ExperimentRegistryError("missing model artifact")
        return metadata, artifact
    except (OSError, json.JSONDecodeError, UnicodeError) as exc:
        raise ExperimentRegistryError("experiment is missing or corrupt") from exc


def list_experiments(root: Path) -> tuple[str, ...]:
    """List valid, complete experiment IDs; corruption is never hidden."""
    checked_root = _checked_root(root, create=False)
    if not checked_root.exists():
        return ()
    identifiers = tuple(sorted(
        entry.name for entry in checked_root.iterdir()
        if _ID.fullmatch(entry.name) is not None
    ))
    for experiment_id in identifiers:
        load_experiment(checked_root, experiment_id)
    return identifiers


def consume_holdout_once(root: Path, experiment_id: str) -> None:
    """Atomically record the one permitted CLI holdout evaluation attempt.

    A failed evaluation after this claim still consumes the holdout: it must
    not become a repeated model-selection opportunity.
    """
    _checked_id(experiment_id)
    checked_root = _checked_root(root, create=False)
    load_experiment(checked_root, experiment_id)
    seals = checked_root / ".holdout-seals"
    try:
        seals.mkdir(mode=0o700, exist_ok=True)
        mode = seals.lstat().st_mode
        if not stat.S_ISDIR(mode) or mode & 0o077:
            raise ExperimentRegistryError("holdout seal directory is unsafe")
        receipt = _canonical({
            "experiment_id": experiment_id,
            "claimed_at_utc": datetime.now(UTC).isoformat(),
            "purpose": "one-time synthetic final holdout evaluation",
        })
        _write_private(seals / experiment_id, receipt)
        _sync_directory(seals)
        _sync_directory(checked_root)
    except FileExistsError as exc:
        raise ExperimentRegistryError("final holdout already evaluated") from exc
