"""Strict local calendar evidence input for read-only readiness reports."""

import json
from datetime import date, datetime, time
from pathlib import Path

from app.modules.research.intraday import ExchangeCalendar, ExchangeSession
from app.modules.research.readiness import CalendarEvidence, TradingHalt


def load_calendar_evidence(path: Path) -> CalendarEvidence:
    """Load an operator-reviewed calendar; never derive holidays from weekdays."""
    if path.stat().st_size > 2_000_000:
        raise ValueError("calendar file exceeds 2 MB")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {
        "source_note",
        "sessions",
        "holidays",
        "halts",
    }:
        raise ValueError("calendar requires source_note, sessions, holidays and halts")
    if (
        not isinstance(data["source_note"], str)
        or not isinstance(data["sessions"], list)
        or not isinstance(data["holidays"], list)
        or not isinstance(data["halts"], list)
    ):
        raise ValueError("invalid calendar fields")
    sessions: list[ExchangeSession] = []
    for item in data["sessions"]:
        if (
            not isinstance(item, dict)
            or set(item) != {"date", "opens_local", "closes_local", "special"}
            or type(item["special"]) is not bool
        ):
            raise ValueError("invalid calendar session")
        sessions.append(
            ExchangeSession(
                date.fromisoformat(item["date"]),
                time.fromisoformat(item["opens_local"]),
                time.fromisoformat(item["closes_local"]),
                item["special"],
            )
        )
    holidays = frozenset(date.fromisoformat(item) for item in data["holidays"])
    halts: list[TradingHalt] = []
    for item in data["halts"]:
        if not isinstance(item, dict) or set(item) != {"start_utc", "end_utc"}:
            raise ValueError("invalid calendar halt")
        halts.append(
            TradingHalt(
                datetime.fromisoformat(item["start_utc"]),
                datetime.fromisoformat(item["end_utc"]),
            )
        )
    return CalendarEvidence(
        ExchangeCalendar(tuple(sessions), holidays),
        tuple(halts),
        data["source_note"],
    )
