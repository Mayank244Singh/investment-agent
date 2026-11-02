"""Honest performance summaries and constrained/unconstrained comparisons."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from math import sqrt
from statistics import pstdev

from app.modules.backtest.costs import CostProfile
from app.modules.backtest.data import PriceData
from app.modules.backtest.engine import (
    SimulationConfig,
    SimulationResult,
    Strategy,
    simulate,
)
from app.modules.backtest.strategies import buy_and_hold

BANNER = (
    "Past results are not predictions or recommendations. Survivorship bias applies "
    "if the universe uses today's constituents. Prices are unadjusted. Short histories "
    "cannot support conclusions. No index benchmark: index data is not stored."
)
UNVERIFIED_COST_WARNING = (
    "Cost profile unverified; synthetic results only. Charges and returns "
    "are illustrative."
)


def banner_for_profile(profile: CostProfile) -> str:
    """Carry the source and cost caveats on every result."""
    return f"{BANNER} {UNVERIFIED_COST_WARNING}" if not profile.verified else BANNER


@dataclass(frozen=True)
class Metrics:
    trading_days: int
    first_date: date
    last_date: date
    initial_capital_paise: int
    final_net_equity_paise: int
    final_gross_equity_paise: int
    gross_return_percent: Decimal
    net_return_percent: Decimal
    total_costs_paise: int
    slippage_impact_paise: int
    cost_drag_paise: int
    max_drawdown_percent: Decimal
    daily_volatility_percent: float
    annualized_return_percent: float | None
    annualized_volatility_percent: float | None
    trade_count: int
    turnover_paise: int
    turnover_percent: Decimal
    mean_exposure_percent: Decimal
    rejected_trade_count: int
    rejection_reasons: tuple[tuple[str, int], ...]
    reduced_quantity: int
    unfilled_quantity: int
    profit_vault_paise: int


def _ratio(numerator: int, denominator: int) -> Decimal:
    if denominator <= 0:
        raise ValueError("non-positive equity prevents a valid ratio")
    return Decimal(numerator) * 100 / denominator


def metrics(result: SimulationResult, config: SimulationConfig) -> Metrics:
    curve = result.equity_curve
    if not curve:
        raise ValueError("no evaluation days")
    initial = config.initial_capital_paise
    net = curve[-1].equity_paise
    costs = sum(fill.charges.total_paise for fill in result.fills)
    drag = costs + result.slippage_impact_paise
    gross = net + drag
    peak = initial
    drawdown = Decimal(0)
    returns: list[float] = []
    prior = initial
    exposures: list[Decimal] = []
    for point in curve:
        if point.equity_paise <= 0:
            raise ValueError("non-positive simulated equity")
        peak = max(peak, point.equity_paise)
        drawdown = max(drawdown, _ratio(peak - point.equity_paise, peak))
        returns.append(float(Decimal(point.equity_paise - prior) / prior))
        exposures.append(_ratio(point.invested_paise, point.equity_paise))
        prior = point.equity_paise
    volatility = pstdev(returns) * 100 if len(returns) >= 2 else 0.0
    annual_return: float | None = None
    annual_volatility: float | None = None
    if len(curve) >= max(252, config.minimum_history_days):
        annual_return = ((net / initial) ** (252 / len(curve)) - 1) * 100
        annual_volatility = volatility * sqrt(252)
    reasons: dict[str, int] = {}
    for rejected in result.rejections:
        reasons[rejected.reason] = reasons.get(rejected.reason, 0) + 1
    turnover = sum(fill.quantity * fill.price_paise for fill in result.fills)
    return Metrics(
        trading_days=len(curve),
        first_date=curve[0].day,
        last_date=curve[-1].day,
        initial_capital_paise=initial,
        final_net_equity_paise=net,
        final_gross_equity_paise=gross,
        gross_return_percent=_ratio(gross - initial, initial),
        net_return_percent=_ratio(net - initial, initial),
        total_costs_paise=costs,
        slippage_impact_paise=result.slippage_impact_paise,
        cost_drag_paise=drag,
        max_drawdown_percent=drawdown,
        daily_volatility_percent=volatility,
        annualized_return_percent=annual_return,
        annualized_volatility_percent=annual_volatility,
        trade_count=len(result.fills),
        turnover_paise=turnover,
        turnover_percent=_ratio(turnover, initial),
        mean_exposure_percent=sum(exposures, Decimal(0)) / len(exposures),
        rejected_trade_count=len(result.rejections),
        rejection_reasons=tuple(sorted(reasons.items())),
        reduced_quantity=sum(
            item.requested - item.filled for item in result.reductions
        ),
        unfilled_quantity=sum(item.unfilled for item in result.reductions),
        profit_vault_paise=result.summary.profit_vault_balance_paise,
    )


@dataclass(frozen=True)
class BenchmarkResult:
    label: str
    metrics: Metrics
    simulation: SimulationResult


@dataclass(frozen=True)
class Report:
    banner: str
    strategy_family: str
    metrics: Metrics
    simulation: SimulationResult
    benchmarks: tuple[BenchmarkResult, ...]
    warnings: tuple[str, ...]


def evaluate(
    data: PriceData,
    profile: CostProfile,
    config: SimulationConfig,
    strategy: Strategy,
    *,
    strategy_family: str,
    benchmark_tranches: int = 1,
    trading_start: date | None = None,
    benchmark_decision_start: date | None = None,
) -> Report:
    """Evaluate one baseline alongside equal-weight and cash comparisons."""
    if len(config.universe) * benchmark_tranches < 10:
        raise ValueError("constrained benchmark needs 10 instruments or tranches")
    actual = simulate(data, profile, config, strategy, trading_start=trading_start)
    constrained = simulate(
        data,
        profile,
        config,
        buy_and_hold(
            config,
            constrained=True,
            tranches_per_instrument=benchmark_tranches,
            decision_start=benchmark_decision_start,
        ),
        trading_start=trading_start,
    )
    unconstrained = simulate(
        data,
        profile,
        config,
        buy_and_hold(
            config,
            constrained=False,
            tranches_per_instrument=benchmark_tranches,
            decision_start=benchmark_decision_start,
        ),
        trading_start=trading_start,
        risk_mode="benchmark_unconstrained",
    )
    cash = simulate(
        data, profile, config, lambda view, summary: (), trading_start=trading_start
    )
    benchmarks = (
        BenchmarkResult(
            "Equal weight constrained: risk validator, costs, slippage",
            metrics(constrained, config),
            constrained,
        ),
        BenchmarkResult(
            "Equal weight unconstrained: no validator; costs, slippage",
            metrics(unconstrained, config),
            unconstrained,
        ),
        BenchmarkResult("Cash: no equity exposure", metrics(cash, config), cash),
    )
    warnings = tuple(
        dict.fromkeys(
            (
                *actual.warnings,
                *((UNVERIFIED_COST_WARNING,) if not profile.verified else ()),
                *(
                    item
                    for benchmark in benchmarks
                    for item in benchmark.simulation.warnings
                ),
            )
        )
    )
    return Report(
        banner_for_profile(profile),
        strategy_family,
        metrics(actual, config),
        actual,
        benchmarks,
        warnings,
    )
