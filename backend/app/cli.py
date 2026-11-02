"""Local operational commands for SQLite backup and guarded restore."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

from app.core.config import PROJECT_ROOT, get_settings
from app.services.backup_service import BackupError, create_backup, restore_backup
from app.services.integrity_service import check_ledger_integrity

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def main(argv: Sequence[str] | None = None) -> int:
    """Run one backup or restore command and return a process exit code."""
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        settings = get_settings()
        database_path = _database_path(arguments.database, settings.database_url)
        if arguments.command == "integrity":
            integrity_result = check_ledger_integrity(database_path)
            if integrity_result.is_valid:
                print("Ledger integrity check passed.")
                return 0
            print("Ledger integrity check failed:", file=sys.stderr)
            for discrepancy in integrity_result.discrepancies:
                print(f"- {discrepancy}", file=sys.stderr)
            return 1

        backup_directory = (
            arguments.backup_directory or settings.backup_directory
        ).expanduser()
        retention_count = (
            arguments.retention
            if arguments.retention is not None
            else settings.backup_retention_count
        )
        if arguments.command == "backup":
            backup_result = create_backup(
                database_path, backup_directory, retention_count
            )
            print(f"Backup created: {backup_result.path}")
            print(f"Older backups pruned: {len(backup_result.pruned_paths)}")
            return 0
        if arguments.command == "restore":
            restore_result = restore_backup(
                arguments.backup,
                database_path,
                backup_directory,
                retention_count,
                _known_migration_revisions(),
                confirm=arguments.confirm,
            )
            print(f"Database restored from: {restore_result.restored_from}")
            print(
                "Pre-restore safety backup: "
                f"{restore_result.safety_backup_path}"
            )
            return 0
    except BackupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error("a command is required")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Investment Agent database tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser(
        "backup", help="Create a consistent SQLite backup"
    )
    _add_common_options(backup_parser)

    integrity_parser = subparsers.add_parser(
        "integrity", help="Verify ledger reconciliation without writing"
    )
    _add_database_option(integrity_parser)

    restore_parser = subparsers.add_parser(
        "restore", help="Restore a verified SQLite backup"
    )
    restore_parser.add_argument("backup", type=Path, help="Backup database to restore")
    restore_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm replacement of the current database",
    )
    _add_common_options(restore_parser)
    return parser


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    _add_database_option(parser)
    parser.add_argument(
        "--backup-directory",
        type=Path,
        help="Backup destination; defaults to BACKUP_DIRECTORY",
    )
    parser.add_argument(
        "--retention",
        type=int,
        help="Number of newest backups to retain",
    )


def _add_database_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        help="SQLite database path; defaults to DATABASE_URL",
    )


def _database_path(override: Path | None, database_url: str) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise BackupError("backup and restore require a file-backed SQLite database")
    if url.database == ":memory:":
        raise BackupError("backup and restore do not support in-memory SQLite")
    path = Path(url.database).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def _known_migration_revisions() -> frozenset[str]:
    config = Config()
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    return frozenset(
        revision.revision
        for revision in scripts.walk_revisions(base="base", head="heads")
    )


if __name__ == "__main__":
    raise SystemExit(main())
