"""Read-only adapter from stored daily observations to research readiness."""

import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from app.modules.research.contracts import Timeframe
from app.modules.research.readiness import (
    ReadinessBar,
    ReadinessPolicy,
    ReadinessReport,
    assess_readiness,
)


class ReadinessError(ValueError):
    """The requested SQLite source cannot be assessed safely."""


def _timestamp(raw: object, label: str, issues: list[str]) -> datetime | None:
    if raw is None:
        return None
    try:
        value = datetime.fromisoformat(str(raw))
    except ValueError:
        issues.append(f"{label}: invalid timestamp")
        return None
    if value.utcoffset() is None:
        issues.append(f"{label}: timezone missing")
    return value


def _identity_verified(
    observation: sqlite3.Row,
    mappings: dict[tuple[object, ...], list[sqlite3.Row]],
) -> bool:
    isin = observation["isin"]
    if (
        not isinstance(isin, str)
        or len(isin) != 12
        or not observation["instrument_symbol"]
    ):
        return False
    trade_date = observation["trade_date"]
    key = (
        observation["instrument_id"],
        observation["provider"],
        observation["provider_instrument_key"],
        observation["symbol"],
        observation["exchange"],
        observation["series"],
    )
    matching = [
        mapping
        for mapping in mappings.get(key, [])
        if mapping["identity_status"] == "verified"
        and mapping["valid_from"] is not None
        and mapping["valid_to"] is not None
        and mapping["valid_from"] <= trade_date <= mapping["valid_to"]
    ]
    return len(matching) == 1


def assess_stored_readiness(
    database: Path,
    policy: ReadinessPolicy,
    *,
    symbols: tuple[str, ...] = (),
    provider: str | None = None,
) -> ReadinessReport:
    """Read a consistent SQLite snapshot with mode=ro; no migrations or writes."""
    path = database.expanduser().resolve()
    if not path.is_file():
        raise ReadinessError("database file does not exist")
    wanted = tuple(sorted({symbol.strip().upper() for symbol in symbols}))
    if any(not symbol for symbol in wanted):
        raise ReadinessError("empty symbol filter")
    issues: list[str] = []
    bars: list[ReadinessBar] = []
    try:
        with closing(
            sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN")
            revision = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            if revision is None or revision[0] != "0004_market_data_foundation":
                raise ReadinessError("Phase 6A market-data migration is required")
            instruments = connection.execute(
                "SELECT id, symbol FROM instruments WHERE series='EQ' ORDER BY symbol"
            ).fetchall()
            selected = tuple(
                row["symbol"]
                for row in instruments
                if not wanted or row["symbol"] in wanted
            )
            if wanted and set(selected) != set(wanted):
                issues.append("one or more requested EQ instruments are unknown")
            if policy.timeframe is Timeframe.DAY:
                mappings: dict[tuple[object, ...], list[sqlite3.Row]] = defaultdict(
                    list
                )
                for mapping in connection.execute("SELECT * FROM instrument_mappings"):
                    key = (
                        mapping["instrument_id"],
                        mapping["provider"],
                        mapping["provider_instrument_key"],
                        mapping["symbol"],
                        mapping["exchange"],
                        mapping["series"],
                    )
                    mappings[key].append(mapping)
                rows = connection.execute(
                    "SELECT o.*, i.symbol AS instrument_symbol, i.isin, "
                    "r.provider AS run_provider, p.id AS canonical_id, "
                    "p.instrument_id AS canonical_instrument_id, "
                    "p.trade_date AS canonical_trade_date, "
                    "p.open_paise AS canonical_open, p.high_paise AS canonical_high, "
                    "p.low_paise AS canonical_low, p.close_paise AS canonical_close, "
                    "p.volume AS canonical_volume "
                    "FROM source_observations o "
                    "LEFT JOIN instruments i ON i.id=o.instrument_id "
                    "LEFT JOIN ingestion_runs r ON r.id=o.ingestion_run_id "
                    "LEFT JOIN daily_prices p ON p.source_observation_id=o.id "
                    "ORDER BY o.instrument_id, o.trade_date, o.id"
                )
                for row in rows:
                    if wanted and row["instrument_symbol"] not in wanted:
                        continue
                    if provider is not None and row["provider"] != provider:
                        continue
                    try:
                        day = date.fromisoformat(str(row["trade_date"]))
                    except ValueError:
                        issues.append(f"observation {row['id']}: invalid trade date")
                        continue
                    if row["run_provider"] != row["provider"]:
                        issues.append(
                            f"observation {row['id']}: ingestion provenance mismatch"
                        )
                    if row["reconciliation"] == "selected":
                        if row["canonical_id"] is None:
                            issues.append(
                                f"observation {row['id']}: lacks canonical link"
                            )
                        elif (
                            row["canonical_instrument_id"] != row["instrument_id"]
                            or row["canonical_trade_date"] != row["trade_date"]
                            or any(
                                row[canonical] != row[source]
                                for canonical, source in (
                                    ("canonical_open", "open_paise"),
                                    ("canonical_high", "high_paise"),
                                    ("canonical_low", "low_paise"),
                                    ("canonical_close", "close_paise"),
                                    ("canonical_volume", "volume"),
                                )
                            )
                        ):
                            issues.append(
                                f"observation {row['id']}: canonical values differ"
                            )
                    elif row["canonical_id"] is not None:
                        issues.append(
                            f"observation {row['id']}: unselected canonical link"
                        )
                    bars.append(
                        ReadinessBar(
                            instrument_id=row["instrument_id"],
                            symbol=row["symbol"],
                            timeframe=Timeframe.DAY,
                            session=day,
                            open_paise=row["open_paise"],
                            high_paise=row["high_paise"],
                            low_paise=row["low_paise"],
                            close_paise=row["close_paise"],
                            volume=row["volume"],
                            provider=row["provider"],
                            source_record_key=row["source_record_key"],
                            identity_verified=_identity_verified(row, mappings),
                            adjustment_status="unverified",
                            available_at_utc=_timestamp(
                                row["available_at_utc"],
                                f"observation {row['id']} availability",
                                issues,
                            ),
                            event_at_utc=_timestamp(
                                row["event_at_utc"],
                                f"observation {row['id']} event",
                                issues,
                            ),
                            retrieved_at_utc=_timestamp(
                                row["retrieved_at_utc"],
                                f"observation {row['id']} retrieval",
                                issues,
                            ),
                            provenance=row["origin"],
                            reconciliation=row["reconciliation"],
                        )
                    )
            # No production intraday table exists. Never re-label daily bars as minutes.
            connection.rollback()
    except sqlite3.Error as exc:
        raise ReadinessError(
            f"cannot read market-data schema: {type(exc).__name__}"
        ) from exc
    effective_policy = (
        replace(policy, provider_access_blocked=True)
        if any(bar.provider == "upstox" for bar in bars)
        else policy
    )
    return assess_readiness(
        tuple(bars),
        effective_policy,
        available_instruments=selected,
        external_issues=tuple(issues),
    )
