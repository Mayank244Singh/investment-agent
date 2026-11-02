"""Daily simulation using a private in-memory copy of the real FIFO ledger."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.ledger import LotRealization
from app.modules.backtest.costs import Charges, CostProfile, charges, slipped_price
from app.modules.backtest.data import Bar, HistoryView, PriceData
from app.modules.portfolio.ledger_service import LedgerService, PortfolioSummary
from app.modules.risk.models import (
    HoldingSnapshot,
    PortfolioSnapshot,
    ProposedTrade,
    RealizedPnlEvent,
    RiskLimits,
)
from app.modules.risk.validator import RiskValidator


@dataclass(frozen=True)
class SimulationConfig:
    initial_capital_paise: int
    universe: tuple[str, ...]
    universe_as_of: date
    risk_limits: RiskLimits
    stop_below_close_percent: Decimal = Decimal("5")
    max_previous_volume_percent: Decimal = Decimal("5")
    corporate_action_mode: Literal["stop", "exclude"] = "stop"
    minimum_history_days: int = 252
    annualisation_warning_days: int = 756
    allow_zero_costs: bool = False

    def __post_init__(self) -> None:
        percents = (
            self.stop_below_close_percent,
            self.max_previous_volume_percent,
            self.risk_limits.max_order_percent,
            self.risk_limits.max_stock_allocation_percent,
            self.risk_limits.max_loss_per_trade_percent,
            self.risk_limits.max_daily_loss_percent,
        )
        if (
            not isinstance(self.initial_capital_paise, int)
            or isinstance(self.initial_capital_paise, bool)
            or self.initial_capital_paise <= 0
            or not self.universe
            or len(set(self.universe)) != len(self.universe)
            or any(
                not isinstance(symbol, str)
                or not symbol
                or symbol != symbol.strip().upper()
                for symbol in self.universe
            )
            or any(
                not isinstance(value, Decimal)
                or not value.is_finite()
                or not 0 < value <= 100
                for value in percents
            )
            or self.stop_below_close_percent >= 100
            or self.corporate_action_mode not in {"stop", "exclude"}
            or self.minimum_history_days < 1
            or self.annualisation_warning_days < self.minimum_history_days
            or not isinstance(self.allow_zero_costs, bool)
        ):
            raise ValueError("invalid simulation configuration")
        try:
            ZoneInfo(self.risk_limits.daily_loss_timezone or "")
        except ZoneInfoNotFoundError as exc:
            raise ValueError("invalid simulated daily-loss timezone") from exc


@dataclass(frozen=True)
class Intent:
    side: Literal["buy", "sell"]
    symbol: str
    quantity: int
    stop_loss_paise: int | None = None


@dataclass(frozen=True)
class Fill:
    day: date
    side: str
    symbol: str
    quantity: int
    price_paise: int
    charges: Charges
    reason: str


@dataclass(frozen=True)
class Rejection:
    day: date
    symbol: str
    reason: str


@dataclass(frozen=True)
class Reduction:
    """Volume-cap allocation; filled is cap-allowed, before risk validation."""

    day: date
    symbol: str
    requested: int
    filled: int
    unfilled: int


@dataclass(frozen=True)
class EquityPoint:
    day: date
    equity_paise: int
    invested_paise: int
    stale_symbols: tuple[str, ...]


@dataclass(frozen=True)
class SimulationResult:
    summary: PortfolioSummary
    fills: tuple[Fill, ...]
    rejections: tuple[Rejection, ...]
    reductions: tuple[Reduction, ...]
    warnings: tuple[str, ...]
    last_marks: tuple[tuple[str, date, int, bool], ...]
    equity_curve: tuple[EquityPoint, ...]
    slippage_impact_paise: int


Strategy = Callable[[HistoryView, PortfolioSummary], Sequence[Intent]]


def _at_open(day: date) -> datetime:
    return datetime.combine(day, time(9, 15), ZoneInfo("Asia/Kolkata")).astimezone(UTC)


def _at_close(day: date) -> datetime:
    return datetime.combine(day, time(15, 30), ZoneInfo("Asia/Kolkata")).astimezone(UTC)


def _snapshot(session: Session, summary: PortfolioSummary) -> PortfolioSnapshot:
    events = tuple(
        RealizedPnlEvent(
            row.realized_pnl_paise,
            row.realized_at_utc.replace(tzinfo=UTC)
            if row.realized_at_utc.tzinfo is None
            else row.realized_at_utc,
        )
        for row in session.scalars(select(LotRealization))
    )
    return PortfolioSnapshot(
        cash_balance_paise=summary.cash_balance_paise,
        profit_vault_balance_paise=summary.profit_vault_balance_paise,
        open_cost_basis_paise=summary.open_cost_basis_paise,
        holdings={
            item.symbol: HoldingSnapshot(item.quantity, item.cost_basis_paise)
            for item in summary.holdings
        },
        realized_events=events,
    )


def simulate(
    data: PriceData,
    profile: CostProfile,
    config: SimulationConfig,
    strategy: Strategy,
    *,
    trading_start: date | None = None,
    risk_mode: Literal["validated", "benchmark_unconstrained"] = "validated",
) -> SimulationResult:
    """Replay bars; never obtains a writable connection to the source database."""
    if risk_mode not in {"validated", "benchmark_unconstrained"}:
        raise ValueError("invalid risk mode")
    if data.origin == "stored" and not profile.verified:
        raise ValueError("broker cost profile is unverified for stored prices")
    if not profile.has_transaction_costs and not config.allow_zero_costs:
        raise ValueError(
            "transaction cost rates are zero; set allow_zero_costs=true explicitly"
        )
    if data.origin == "stored" and profile.slippage_bps <= 0:
        raise ValueError("stored-price backtests require positive slippage")
    if not data.days:
        raise ValueError("no bars in configured universe")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    fills: list[Fill] = []
    rejections: list[Rejection] = []
    reductions: list[Reduction] = []
    warnings: list[str] = []
    if not profile.has_transaction_costs:
        warnings.append(
            "Zero transaction costs explicitly allowed; results omit trading charges"
        )
    marks: dict[str, tuple[date, int, bool]] = {}
    previous: dict[str, Bar] = {}
    excluded: set[str] = set()
    stops: dict[str, int] = {}
    queued: Sequence[Intent] = ()
    equity_curve: list[EquityPoint] = []
    slippage_impact = 0
    validator = RiskValidator(config.risk_limits)
    evaluation_days = sum(
        trading_start is None or day >= trading_start for day in data.days
    )
    if evaluation_days < max(252, config.minimum_history_days):
        warnings.append("Insufficient history for annualised metrics")
    elif evaluation_days < config.annualisation_warning_days:
        warnings.append(
            f"Fewer than {config.annualisation_warning_days} trading days of history"
        )
    warnings.append("Universe is limited to instruments present in the supplied data")

    with Session(engine) as session:
        ledger = LedgerService(session)
        ledger.record_deposit(config.initial_capital_paise, _at_open(data.days[0]))

        def execute(
            intent: Intent, day: date, bar: Bar, reference: int, reason: str
        ) -> None:
            nonlocal slippage_impact
            side = intent.side
            quantity = intent.quantity
            prior = previous.get(intent.symbol)
            if prior is None:
                rejections.append(
                    Rejection(day, intent.symbol, "no completed prior-day volume")
                )
                return
            cap = int(Decimal(prior.volume) * config.max_previous_volume_percent / 100)
            actual = min(quantity, cap)
            if actual < quantity:
                reductions.append(
                    Reduction(day, intent.symbol, quantity, actual, quantity - actual)
                )
            if actual <= 0:
                rejections.append(
                    Rejection(day, intent.symbol, "previous-day volume cap is zero")
                )
                return
            price = slipped_price(profile, side, reference)
            fee = charges(profile, side, actual, price)
            if risk_mode == "benchmark_unconstrained" and side == "buy":
                available = ledger.get_summary().cash_balance_paise
                lower, upper = 0, actual
                while lower < upper:
                    candidate = (lower + upper + 1) // 2
                    candidate_fee = charges(profile, side, candidate, price)
                    if candidate * price + candidate_fee.total_paise <= available:
                        lower = candidate
                    else:
                        upper = candidate - 1
                actual = lower
                if actual:
                    fee = charges(profile, side, actual, price)
                if actual < min(quantity, cap):
                    reductions.append(
                        Reduction(
                            day,
                            intent.symbol,
                            min(quantity, cap),
                            actual,
                            min(quantity, cap) - actual,
                        )
                    )
                if actual == 0:
                    rejections.append(
                        Rejection(
                            day, intent.symbol, "unconstrained benchmark lacks cash"
                        )
                    )
                    return
            proposed = ProposedTrade(
                side,
                intent.symbol,
                actual,
                price,
                _at_open(day),
                intent.stop_loss_paise,
                fee.total_paise,
            )
            if risk_mode == "validated":
                result = validator.validate(
                    proposed, _snapshot(session, ledger.get_summary())
                )
                if not result.approved:
                    rejections.extend(
                        Rejection(day, intent.symbol, item.code.value)
                        for item in result.reasons
                    )
                    return
            if side == "buy":
                ledger.record_buy(
                    intent.symbol, actual, price, _at_open(day), fee.total_paise
                )
                assert intent.stop_loss_paise is not None
                stops[intent.symbol] = max(
                    stops.get(intent.symbol, 0), intent.stop_loss_paise
                )
            else:
                ledger.record_sell(
                    intent.symbol, actual, price, _at_open(day), fee.total_paise
                )
                if not any(
                    h.symbol == intent.symbol for h in ledger.get_summary().holdings
                ):
                    stops.pop(intent.symbol, None)
            fills.append(Fill(day, side, intent.symbol, actual, price, fee, reason))
            slippage_impact += abs(price - reference) * actual

        for day in data.days:
            today = {symbol: data.get(symbol, day) for symbol in config.universe}
            held = {h.symbol for h in ledger.get_summary().holdings}
            for symbol, bar in today.items():
                if bar is None:
                    if symbol in held and symbol in marks:
                        mark_day, price, _ = marks[symbol]
                        marks[symbol] = (mark_day, price, True)
                        warnings.append(
                            f"{day}: missing held bar for {symbol}; last close stale"
                        )
                    continue
                prior = previous.get(symbol)
                if prior is not None and bar.previous_close_paise != prior.close_paise:
                    message = f"{day}: suspected corporate action for {symbol}"
                    if symbol in held or config.corporate_action_mode == "stop":
                        raise ValueError(message)
                    excluded.add(symbol)
                    warnings.append(message + "; excluded from flagged date")

            # Open: execute yesterday's decisions against today's open, if tradable.
            for intent in (
                queued if trading_start is None or day >= trading_start else ()
            ):
                bar = today.get(intent.symbol)
                if intent.symbol not in config.universe or intent.symbol in excluded:
                    rejections.append(
                        Rejection(
                            day, intent.symbol, "symbol excluded or outside universe"
                        )
                    )
                elif bar is None:
                    rejections.append(
                        Rejection(day, intent.symbol, "missing execution bar")
                    )
                else:
                    execute(intent, day, bar, bar.open_paise, "strategy")

            # Low is used only after the open. A gap below the stop fills at open.
            for symbol, stop in tuple(stops.items()):
                bar = today.get(symbol)
                if bar is not None and bar.low_paise <= stop:
                    holding = next(
                        (
                            h
                            for h in ledger.get_summary().holdings
                            if h.symbol == symbol
                        ),
                        None,
                    )
                    if holding is not None:
                        execute(
                            Intent("sell", symbol, holding.quantity),
                            day,
                            bar,
                            min(stop, bar.open_paise),
                            "stop",
                        )

            for symbol, bar in today.items():
                if bar is not None and symbol not in excluded:
                    marks[symbol] = (day, bar.close_paise, False)
                    previous[symbol] = bar
            # Strategy sees a fresh bounded view after today's close.
            view = data.view(day)
            queued = tuple(strategy(view, ledger.get_summary()))
            for intent in queued:
                if (
                    not isinstance(intent.quantity, int)
                    or isinstance(intent.quantity, bool)
                    or intent.quantity <= 0
                ):
                    raise ValueError("strategy quantity must be positive")
                if intent.side == "buy" and intent.stop_loss_paise is None:
                    bar = today.get(intent.symbol)
                    if bar is None:
                        raise ValueError("cannot derive stop without today's close")
                    stop = int(
                        Decimal(bar.close_paise)
                        * (100 - config.stop_below_close_percent)
                        / 100
                    )
                    queued = tuple(
                        Intent(
                            item.side,
                            item.symbol,
                            item.quantity,
                            stop if item is intent else item.stop_loss_paise,
                        )
                        for item in queued
                    )

            if trading_start is None or day >= trading_start:
                summary = ledger.get_summary()
                quantities = {
                    holding.symbol: holding.quantity for holding in summary.holdings
                }
                invested = sum(
                    quantity * marks[symbol][1]
                    for symbol, quantity in quantities.items()
                    if symbol in marks
                )
                stale = tuple(
                    sorted(
                        symbol
                        for symbol in quantities
                        if symbol not in marks or marks[symbol][2]
                    )
                )
                equity_curve.append(
                    EquityPoint(
                        day, summary.cash_balance_paise + invested, invested, stale
                    )
                )

        summary = ledger.get_summary()
    engine.dispose()
    return SimulationResult(
        summary,
        tuple(fills),
        tuple(rejections),
        tuple(reductions),
        tuple(warnings),
        tuple((symbol, *mark) for symbol, mark in sorted(marks.items())),
        tuple(equity_curve),
        slippage_impact,
    )
