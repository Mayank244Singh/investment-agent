"""Explicit virtual-account seed; never touches the configured production DB."""

import argparse
from pathlib import Path

from app.core.config import DEFAULT_DATABASE_PATH
from app.services.virtual_trading_service import seed_virtual_account


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed a migrated isolated virtual database"
    )
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument(
        "--production-database", type=Path, default=DEFAULT_DATABASE_PATH
    )
    arguments = parser.parse_args()
    seeded = seed_virtual_account(
        arguments.database, f"sqlite:///{arguments.production_database.resolve()}"
    )
    print(
        "Virtual account seeded with ₹10,000 synthetic capital."
        if seeded
        else "Virtual account already seeded; no change."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
