"""Two deterministic baseline strategies for daily-bar comparisons."""

from datetime import date
from decimal import Decimal

from app.modules.backtest.data import HistoryView
from app.modules.backtest.engine import Intent, SimulationConfig, Strategy
from app.modules.portfolio.ledger_service import PortfolioSummary


def _percent(value: int, percent: Decimal | None) -> int:
    return int(Decimal(value) * percent / 100) if percent is not None else 0


def _buy_quantity(
    config: SimulationConfig,
    summary: PortfolioSummary,
    symbol: str,
    close_paise: int,
    target_paise: int,
    available_paise: int,
    reserved_cost_paise: int = 0,
) -> int:
    """Size using last close; the unchanged validator checks the next-open fill."""
    limits = config.risk_limits
    stop = int(Decimal(close_paise) * (100 - config.stop_below_close_percent) / 100)
    if stop <= 0 or stop >= close_paise:
        return 0
    held_cost = reserved_cost_paise + next(
        (h.cost_basis_paise for h in summary.holdings if h.symbol == symbol), 0
    )
    risk_capital = summary.risk_capital_paise
    risk_per_share = close_paise - stop
    budget = min(
        target_paise,
        _percent(risk_capital, limits.max_order_percent),
        _percent(risk_capital, limits.max_stock_allocation_percent) - held_cost,
        _percent(risk_capital, limits.max_loss_per_trade_percent)
        * close_paise
        // risk_per_share,
        available_paise,  # next-open gap and actual charges are checked at fill
    )
    return max(0, budget // close_paise)


def buy_and_hold(
    config: SimulationConfig,
    *,
    constrained: bool = True,
    tranches_per_instrument: int = 1,
    decision_start: date | None = None,
) -> Strategy:
    """One equal-weight entry decision; optional tranches for the risk benchmark."""
    if tranches_per_instrument < 1:
        raise ValueError("tranches_per_instrument must be positive")
    acted = False

    def decide(view: HistoryView, summary: PortfolioSummary) -> tuple[Intent, ...]:
        nonlocal acted
        if acted or (decision_start is not None and view.as_of < decision_start):
            return ()
        acted = True
        intents: list[Intent] = []
        available = summary.deployable_cash_paise
        target = config.initial_capital_paise // (
            len(config.universe) * tranches_per_instrument
        )
        for symbol in config.universe:
            bar = view.get(symbol, view.as_of)
            if bar is None:
                continue
            reserved = 0
            stop = int(
                Decimal(bar.close_paise) * (100 - config.stop_below_close_percent) / 100
            )
            for _ in range(tranches_per_instrument):
                quantity = (
                    _buy_quantity(
                        config,
                        summary,
                        symbol,
                        bar.close_paise,
                        target,
                        available,
                        reserved,
                    )
                    if constrained
                    else max(0, min(target, available) // bar.close_paise)
                )
                if quantity:
                    intents.append(Intent("buy", symbol, quantity, stop))
                    available -= quantity * bar.close_paise
                    reserved += quantity * bar.close_paise
        return tuple(intents)

    return decide


def momentum_ranking(
    config: SimulationConfig,
    *,
    lookback_days: int,
    top_k: int,
    rebalance_days: int,
    decision_start: date | None = None,
) -> Strategy:
    """Rank trailing closes after each rebalance close, with deterministic ties."""
    if lookback_days < 1 or top_k < 1 or rebalance_days < 1:
        raise ValueError("momentum parameters must be positive")
    eligible_count = 0

    def decide(view: HistoryView, summary: PortfolioSummary) -> tuple[Intent, ...]:
        nonlocal eligible_count
        if decision_start is not None and view.as_of < decision_start:
            return ()
        ranked: list[tuple[Decimal, str, int]] = []
        for symbol in config.universe:
            bars = view.bars(symbol)
            if len(bars) < lookback_days + 1 or bars[-1].day != view.as_of:
                continue
            score = (
                Decimal(bars[-1].close_paise) / bars[-lookback_days - 1].close_paise - 1
            )
            ranked.append((score, symbol, bars[-1].close_paise))
        if not ranked:
            return ()
        eligible_count += 1
        if (eligible_count - 1) % rebalance_days:
            return ()
        ranked.sort(key=lambda item: (-item[0], item[1]))
        chosen = {symbol for _, symbol, _ in ranked[:top_k]}
        held = {item.symbol: item for item in summary.holdings}
        intents: list[Intent] = [
            Intent("sell", symbol, holding.quantity)
            for symbol, holding in sorted(held.items())
            if symbol not in chosen
        ]
        available = summary.deployable_cash_paise
        target = config.initial_capital_paise // max(top_k, 1)
        for _, symbol, close in ranked[:top_k]:
            if symbol in held:
                continue
            quantity = _buy_quantity(config, summary, symbol, close, target, available)
            if quantity:
                stop = int(
                    Decimal(close) * (100 - config.stop_below_close_percent) / 100
                )
                intents.append(Intent("buy", symbol, quantity, stop))
                available -= quantity * close
        return tuple(intents)

    return decide
