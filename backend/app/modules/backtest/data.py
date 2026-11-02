"""Read-only stored price loading and bounded strategy history."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db.models.prices import DailyPrice, Instrument


@dataclass(frozen=True)
class Bar:
    symbol: str
    day: date
    open_paise: int
    high_paise: int
    low_paise: int
    close_paise: int
    previous_close_paise: int
    volume: int

    def __post_init__(self) -> None:
        values = (
            self.open_paise,
            self.high_paise,
            self.low_paise,
            self.close_paise,
            self.previous_close_paise,
            self.volume,
        )
        if any(
            not isinstance(value, int) or isinstance(value, bool) for value in values
        ):
            raise ValueError("price bars require integer paise and volume")
        if (
            not self.symbol
            or self.symbol != self.symbol.strip().upper()
            or min(
                self.open_paise,
                self.high_paise,
                self.low_paise,
                self.close_paise,
                self.previous_close_paise,
            )
            <= 0
            or self.volume < 0
        ):
            raise ValueError("invalid price bar")
        if not self.low_paise <= self.open_paise <= self.high_paise:
            raise ValueError("open outside daily range")
        if not self.low_paise <= self.close_paise <= self.high_paise:
            raise ValueError("close outside daily range")


class PriceData:
    """Immutable bars; stored provenance gates unverified cost profiles."""

    def __init__(self, bars: Iterable[Bar], *, origin: Literal["synthetic", "stored"]):
        if origin not in {"synthetic", "stored"}:
            raise ValueError("invalid price-data origin")
        self.origin = origin
        by_key: dict[tuple[str, date], Bar] = {}
        for bar in bars:
            key = (bar.symbol, bar.day)
            if key in by_key:
                raise ValueError(f"duplicate bar: {key}")
            by_key[key] = bar
        self._bars = MappingProxyType(by_key)
        self.days = tuple(sorted({day for _, day in by_key}))

    def get(self, symbol: str, day: date) -> Bar | None:
        return self._bars.get((symbol, day))

    def ordered_bars(self) -> tuple[Bar, ...]:
        """Stable ordering for hashes and out-of-sample slices."""
        return tuple(self._bars[key] for key in sorted(self._bars))

    def through(self, last_day: date, *, first_day: date | None = None) -> "PriceData":
        return PriceData(
            (
                bar
                for bar in self.ordered_bars()
                if bar.day <= last_day and (first_day is None or bar.day >= first_day)
            ),
            origin=self.origin,
        )

    def view(self, day: date) -> "HistoryView":
        return HistoryView(
            day,
            tuple(bar for (_, bar_day), bar in self._bars.items() if bar_day <= day),
        )


class HistoryView:
    """A strategy receives copied history through its decision date only."""

    def __init__(self, as_of: date, bars: tuple[Bar, ...]):
        self.as_of = as_of
        self._bars = bars

    def bars(self, symbol: str) -> tuple[Bar, ...]:
        return tuple(
            sorted(
                (bar for bar in self._bars if bar.symbol == symbol),
                key=lambda bar: bar.day,
            )
        )

    def get(self, symbol: str, day: date) -> Bar | None:
        if day > self.as_of:
            raise ValueError("future bars are not available to strategies")
        return next(
            (bar for bar in self._bars if bar.symbol == symbol and bar.day == day), None
        )


def load_stored_prices(database_path: Path, universe: tuple[str, ...]) -> PriceData:
    """Load only EQ bars via SQLite mode=ro; never open the app's write engine."""
    if not database_path.is_file():
        raise FileNotFoundError(database_path)
    engine: Engine = create_engine(f"sqlite:///file:{database_path}?mode=ro&uri=true")

    @event.listens_for(engine, "connect")
    def enforce_read_only(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA query_only=ON")
        cursor.close()

    try:
        with Session(engine) as session:
            rows = session.execute(
                select(Instrument.symbol, DailyPrice)
                .join(DailyPrice, DailyPrice.instrument_id == Instrument.id)
                .where(Instrument.series == "EQ", Instrument.symbol.in_(universe))
            )
            bars = [
                Bar(
                    symbol,
                    row.trade_date,
                    row.open_paise,
                    row.high_paise,
                    row.low_paise,
                    row.close_paise,
                    row.previous_close_paise,
                    row.volume,
                )
                for symbol, row in rows
            ]
        return PriceData(bars, origin="stored")
    finally:
        engine.dispose()
