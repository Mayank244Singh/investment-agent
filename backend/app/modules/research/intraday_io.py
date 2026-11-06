"""Synthetic-only intraday fixture reader; no credentials, database or network."""

import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from app.modules.research.contracts import Timeframe
from app.modules.research.intraday import (
    ExchangeCalendar,
    ExchangeSession,
    IntradayCandle,
    IntradayConfig,
)


def load_synthetic_intraday(
    path: Path,
) -> tuple[tuple[IntradayCandle, ...], IntradayConfig]:
    if path.stat().st_size > 5_000_000:
        raise ValueError("synthetic intraday file exceeds 5 MB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("synthetic") is not True:
            raise ValueError("intraday input must explicitly be synthetic")
        calendar = ExchangeCalendar(
            sessions=tuple(
                ExchangeSession(
                    day=date.fromisoformat(item["date"]),
                    opens_local=time.fromisoformat(item["opens_local"]),
                    closes_local=time.fromisoformat(item["closes_local"]),
                    special=item.get("special", False),
                ) for item in payload["sessions"]
            ),
            holidays=frozenset(
                date.fromisoformat(item) for item in payload.get("holidays", [])
            ),
        )
        config = IntradayConfig(
            dataset_version=payload["dataset_version"],
            calendar=calendar,
            timeframe=Timeframe(payload["timeframe"]),
            built_at_utc=datetime.fromisoformat(payload["built_at_utc"]),
            horizons_minutes=tuple(payload.get("horizons_minutes", (5, 15, 30, 60))),
            decision_lag_seconds=payload.get("decision_lag_seconds", 0),
            momentum_bars=payload.get("momentum_bars", 3),
            rolling_window_bars=payload.get("rolling_window_bars", 5),
            opening_range_minutes=payload.get("opening_range_minutes", 15),
            required_features=tuple(payload.get(
                "required_features", ("session_vwap_paise",)
            )),
        )
        candles = tuple(_candle(item, config.timeframe) for item in payload["candles"])
        return candles, config
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("invalid synthetic intraday input") from exc


def _candle(item: dict[str, Any], timeframe: Timeframe) -> IntradayCandle:
    return IntradayCandle(
        instrument_id=item["instrument_id"], timeframe=timeframe,
        start_utc=datetime.fromisoformat(item["start_utc"]),
        end_utc=datetime.fromisoformat(item["end_utc"]),
        available_at_utc=(
            datetime.fromisoformat(item["available_at_utc"])
            if item["available_at_utc"] is not None else None
        ),
        is_complete=item["is_complete"],
        open_paise=item["open_paise"], high_paise=item["high_paise"],
        low_paise=item["low_paise"], close_paise=item["close_paise"],
        volume=item["volume"], provider=item["provider"],
        source_record_key=item["source_record_key"],
        adjustment_status=item["adjustment_status"],
        identity_verified=item["identity_verified"],
        corporate_action_flag=item.get("corporate_action_flag", False),
    )
