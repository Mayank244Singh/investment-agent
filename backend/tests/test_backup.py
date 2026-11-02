"""SQLite online backup, retention, and guarded restore tests."""

import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.cli import main
from app.services.backup_service import (
    BackupIntegrityError,
    BackupMigrationError,
    RestoreConfirmationRequired,
    create_backup,
    restore_backup,
)

CURRENT_REVISION = "0002_ledger_idempotency"
KNOWN_REVISIONS = {"0001_initial_ledger", CURRENT_REVISION}
BACKUP_TIME = datetime(2026, 11, 2, 1, 0, tzinfo=UTC)


def test_cli_backup_and_restore_round_trip_uses_safety_backup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "current.db"
    backups = tmp_path / "backups"
    _create_database(database, "before backup")

    assert (
        main(
            [
                "backup",
                "--database",
                str(database),
                "--backup-directory",
                str(backups),
                "--retention",
                "10",
            ]
        )
        == 0
    )
    backup = next(backups.glob("investment_agent_*.db"))
    _insert_value(database, "after backup")

    assert (
        main(
            [
                "restore",
                str(backup),
                "--confirm",
                "--database",
                str(database),
                "--backup-directory",
                str(backups),
                "--retention",
                "10",
            ]
        )
        == 0
    )

    assert _read_values(database) == ["before backup"]
    safety_backup = next(backups.glob("pre_restore_*.db"))
    assert _read_values(safety_backup) == ["before backup", "after backup"]
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


def test_retention_keeps_only_the_newest_configured_backups(tmp_path: Path) -> None:
    database = tmp_path / "current.db"
    backups = tmp_path / "backups"
    _create_database(database, "record")

    results = [
        create_backup(
            database,
            backups,
            retention_count=2,
            now=BACKUP_TIME + timedelta(seconds=offset),
        )
        for offset in range(3)
    ]

    retained = sorted(backups.glob("investment_agent_*.db"))
    assert retained == [results[1].path, results[2].path]
    assert results[2].pruned_paths == (results[0].path,)
    assert not results[0].path.exists()


def test_restore_requires_explicit_confirmation_and_writes_nothing(
    tmp_path: Path,
) -> None:
    database = tmp_path / "current.db"
    backups = tmp_path / "backups"
    _create_database(database, "current")
    backup = create_backup(database, backups, 10, now=BACKUP_TIME).path
    _insert_value(database, "must remain")

    with pytest.raises(RestoreConfirmationRequired, match="--confirm"):
        restore_backup(
            backup,
            database,
            backups,
            10,
            KNOWN_REVISIONS,
            confirm=False,
        )

    assert _read_values(database) == ["current", "must remain"]
    assert list(backups.glob("pre_restore_*.db")) == []


def test_restore_rejects_unknown_newer_migration_before_safety_backup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "current.db"
    backups = tmp_path / "backups"
    _create_database(database, "current")
    backup = create_backup(database, backups, 10, now=BACKUP_TIME).path
    with sqlite3.connect(backup) as connection:
        connection.execute(
            "UPDATE alembic_version SET version_num = ?", ("9999_future",)
        )

    with pytest.raises(BackupMigrationError, match="newer than or unknown"):
        restore_backup(
            backup,
            database,
            backups,
            10,
            KNOWN_REVISIONS,
            confirm=True,
        )

    assert _read_values(database) == ["current"]
    assert list(backups.glob("pre_restore_*.db")) == []


def test_restore_rejects_corrupt_backup_without_changing_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "current.db"
    corrupt_backup = tmp_path / "corrupt.db"
    backups = tmp_path / "backups"
    _create_database(database, "current")
    corrupt_backup.write_bytes(b"not a SQLite database")

    with pytest.raises(BackupIntegrityError):
        restore_backup(
            corrupt_backup,
            database,
            backups,
            10,
            KNOWN_REVISIONS,
            confirm=True,
        )

    assert _read_values(database) == ["current"]
    assert list(backups.glob("pre_restore_*.db")) == []


def _create_database(path: Path, value: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE alembic_version (
                version_num VARCHAR(32) NOT NULL PRIMARY KEY
            );
            CREATE TABLE records (
                id INTEGER PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO alembic_version (version_num) VALUES (?)",
            (CURRENT_REVISION,),
        )
        connection.execute("INSERT INTO records (value) VALUES (?)", (value,))


def _insert_value(path: Path, value: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO records (value) VALUES (?)", (value,))


def _read_values(path: Path) -> list[str]:
    uri = f"{path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return [str(row[0]) for row in connection.execute("SELECT value FROM records")]
