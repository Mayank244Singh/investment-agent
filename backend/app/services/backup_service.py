"""Consistent SQLite backup, retention, and guarded restore operations."""

import os
import sqlite3
import tempfile
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class BackupError(Exception):
    """Base error for backup and restore operations."""


class BackupIntegrityError(BackupError):
    """A database file failed SQLite integrity validation."""


class BackupMigrationError(BackupError):
    """A backup migration revision is not supported by this code."""


class RestoreConfirmationRequired(BackupError):
    """A restore was requested without explicit confirmation."""


@dataclass(frozen=True)
class BackupResult:
    """Created backup and any older files removed by retention."""

    path: Path
    pruned_paths: tuple[Path, ...]


@dataclass(frozen=True)
class RestoreResult:
    """Completed restore and the safety backup made beforehand."""

    database_path: Path
    restored_from: Path
    safety_backup_path: Path


def create_backup(
    database_path: Path,
    backup_directory: Path,
    retention_count: int,
    *,
    prefix: str = "investment_agent",
    now: datetime | None = None,
) -> BackupResult:
    """Create and verify a consistent backup using SQLite's online API."""
    source = database_path.expanduser().resolve()
    destination_directory = backup_directory.expanduser().resolve()
    _require_source_database(source)
    _require_positive_retention(retention_count)
    destination_directory.mkdir(parents=True, exist_ok=True)

    timestamp = (now or datetime.now(UTC)).astimezone(UTC)
    filename = f"{prefix}_{timestamp.strftime('%Y%m%dT%H%M%S%fZ')}.db"
    destination = destination_directory / filename
    if destination.exists():
        raise BackupError(f"backup already exists: {destination}")

    temporary = _temporary_path(destination_directory, ".backup-")
    try:
        _online_copy(source, temporary)
        verify_sqlite_integrity(temporary)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    pruned = _prune_backups(destination_directory, prefix, retention_count)
    return BackupResult(destination, pruned)


def restore_backup(
    backup_path: Path,
    database_path: Path,
    backup_directory: Path,
    retention_count: int,
    known_migration_revisions: Collection[str],
    *,
    confirm: bool,
    now: datetime | None = None,
) -> RestoreResult:
    """Verify, safety-backup, and atomically restore one SQLite database."""
    if not confirm:
        raise RestoreConfirmationRequired("restore requires the --confirm flag")

    source_backup = backup_path.expanduser().resolve()
    destination = database_path.expanduser().resolve()
    _require_source_database(source_backup)
    _require_source_database(destination)
    if source_backup.samefile(destination):
        raise BackupError("backup and destination database must be different files")

    verify_sqlite_integrity(source_backup)
    verify_migration_revision(source_backup, known_migration_revisions)

    safety_backup = create_backup(
        destination,
        backup_directory,
        retention_count,
        prefix="pre_restore",
        now=now,
    )

    temporary = _temporary_path(destination.parent, ".restore-")
    try:
        _online_copy(source_backup, temporary)
        verify_sqlite_integrity(temporary)
        verify_migration_revision(temporary, known_migration_revisions)
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    return RestoreResult(destination, source_backup, safety_backup.path)


def verify_sqlite_integrity(database_path: Path) -> None:
    """Fail unless SQLite and foreign-key integrity checks both pass."""
    try:
        with _open_read_only(database_path) as connection:
            results = [
                str(row[0])
                for row in connection.execute("PRAGMA integrity_check")
            ]
            if results != ["ok"]:
                raise BackupIntegrityError(
                    "SQLite integrity check failed: " + "; ".join(results)
                )
            foreign_key_errors = list(connection.execute("PRAGMA foreign_key_check"))
    except sqlite3.DatabaseError as exc:
        raise BackupIntegrityError("SQLite integrity check could not run") from exc
    if foreign_key_errors:
        raise BackupIntegrityError(
            f"SQLite foreign-key check found {len(foreign_key_errors)} violation(s)"
        )


def verify_migration_revision(
    database_path: Path, known_migration_revisions: Collection[str]
) -> str:
    """Reject a backup whose Alembic revision is unknown to this code."""
    try:
        with _open_read_only(database_path) as connection:
            rows = list(connection.execute("SELECT version_num FROM alembic_version"))
    except sqlite3.DatabaseError as exc:
        raise BackupMigrationError(
            "backup does not contain a readable Alembic revision"
        ) from exc
    if len(rows) != 1:
        raise BackupMigrationError("backup must contain exactly one Alembic revision")
    revision = str(rows[0][0])
    if revision not in known_migration_revisions:
        raise BackupMigrationError(
            "backup migration revision is newer than or unknown to this code: "
            f"{revision}"
        )
    return revision


def _online_copy(source: Path, destination: Path) -> None:
    with _open_read_only(source) as source_connection:
        with sqlite3.connect(destination) as destination_connection:
            source_connection.backup(destination_connection)


def _open_read_only(database_path: Path) -> sqlite3.Connection:
    uri = f"{database_path.expanduser().resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _temporary_path(directory: Path, prefix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=".db", dir=directory)
    os.close(descriptor)
    return Path(name)


def _prune_backups(
    backup_directory: Path, prefix: str, retention_count: int
) -> tuple[Path, ...]:
    candidates = sorted(backup_directory.glob(f"{prefix}_*.db"))
    expired = candidates[:-retention_count]
    for path in expired:
        path.unlink()
    return tuple(expired)


def _require_source_database(database_path: Path) -> None:
    if not database_path.is_file():
        raise BackupError(f"SQLite database does not exist: {database_path}")


def _require_positive_retention(retention_count: int) -> None:
    if retention_count <= 0:
        raise BackupError("backup retention count must be positive")
