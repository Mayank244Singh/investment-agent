"""Strict synthetic-only JSON input for offline research validation."""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.modules.research.contracts import StrategyFamily
from app.modules.research.core import (
    DEFAULT_REQUIRED_FEATURES,
    ResearchConfig,
    ResearchObservation,
)


def load_synthetic_research(
    path: Path,
) -> tuple[tuple[ResearchObservation, ...], ResearchConfig]:
    """Read invented fixture facts; no database, network, or inferred timestamps."""
    if path.stat().st_size > 5_000_000:
        raise ValueError("synthetic research file exceeds 5 MB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("synthetic") is not True:
            raise ValueError("research input must explicitly be synthetic")
        sessions = tuple(date.fromisoformat(day) for day in payload["sessions"])
        decisions = {
            date.fromisoformat(day): datetime.fromisoformat(timestamp)
            for day, timestamp in payload["decision_times_utc"].items()
        }
        config = ResearchConfig(
            dataset_version=payload["dataset_version"],
            sessions=sessions,
            decision_times_utc=decisions,
            built_at_utc=datetime.fromisoformat(payload["built_at_utc"]),
            window=payload.get("window", 5),
            benchmark_instrument_id=payload.get("benchmark_instrument_id"),
            require_benchmark=payload.get("require_benchmark", False),
            required_features=tuple(payload.get(
                "required_features", DEFAULT_REQUIRED_FEATURES
            )),
            strategy=(StrategyFamily(payload["strategy"])
                      if payload.get("strategy") is not None else None),
            horizons_sessions=tuple(payload.get(
                "horizons_sessions", (5, 10, 60, 120)
            )),
        )
        observations = tuple(
            _observation(item) for item in payload["observations"]
        )
        return observations, config
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("invalid synthetic research input") from exc


def _observation(item: dict[str, Any]) -> ResearchObservation:
    return ResearchObservation(
        instrument_id=item["instrument_id"],
        session=date.fromisoformat(item["session"]),
        close_paise=item["close_paise"],
        volume=item["volume"],
        provider=item["provider"],
        source_record_key=item["source_record_key"],
        available_at_utc=(
            datetime.fromisoformat(item["available_at_utc"])
            if item["available_at_utc"] is not None else None
        ),
        adjustment_status=item["adjustment_status"],
        identity_valid_from=(
            date.fromisoformat(item["identity_valid_from"])
            if item["identity_valid_from"] is not None else None
        ),
        identity_valid_to=(
            date.fromisoformat(item["identity_valid_to"])
            if item["identity_valid_to"] is not None else None
        ),
        identity_verified=item["identity_verified"],
        corporate_action_flag=item.get("corporate_action_flag", False),
        open_paise=item.get("open_paise"),
        high_paise=item.get("high_paise"),
        low_paise=item.get("low_paise"),
    )
