"""Configuration, out-of-sample windows, and local reproducible run records."""

import fcntl
import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from app.modules.backtest.costs import CostProfile
from app.modules.backtest.data import Bar, PriceData
from app.modules.backtest.engine import SimulationConfig, Strategy
from app.modules.backtest.report import Report, evaluate
from app.modules.backtest.strategies import buy_and_hold, momentum_ranking
from app.modules.risk.models import RiskLimits


@dataclass(frozen=True)
class RunSettings:
    simulation: SimulationConfig
    strategy_family: Literal["buy_and_hold", "momentum"]
    momentum_lookback_days: int = 20
    momentum_top_k: int = 3
    momentum_rebalance_days: int = 20
    benchmark_tranches: int = 1
    window_size_days: int | None = None

    def __post_init__(self) -> None:
        if (
            self.momentum_lookback_days < 1
            or self.momentum_top_k < 1
            or self.momentum_rebalance_days < 1
            or self.benchmark_tranches < 1
            or self.window_size_days is not None
            and self.window_size_days < 1
        ):
            raise ValueError("invalid run settings")


@dataclass(frozen=True)
class WindowReport:
    start: date
    end: date
    report: Report


@dataclass(frozen=True)
class RunOutcome:
    trial_number: int
    strategy_family: str
    banner: str
    windows: tuple[WindowReport, ...]
    record_path: Path


def load_run_settings(path: Path) -> RunSettings:
    """Read a dated local JSON config; all percentages are decimal strings."""
    value = json.loads(path.read_text(encoding="utf-8"))
    risk = value["risk_limits"]
    limits = RiskLimits(
        Decimal(risk["max_order_percent"]),
        Decimal(risk["max_stock_allocation_percent"]),
        Decimal(risk["max_loss_per_trade_percent"]),
        Decimal(risk["max_daily_loss_percent"]),
        risk["daily_loss_timezone"],
    )
    simulation = SimulationConfig(
        initial_capital_paise=value["initial_capital_paise"],
        universe=tuple(value["universe"]),
        universe_as_of=date.fromisoformat(value["universe_as_of"]),
        risk_limits=limits,
        stop_below_close_percent=Decimal(value.get("stop_below_close_percent", "5")),
        max_previous_volume_percent=Decimal(value["max_previous_volume_percent"]),
        corporate_action_mode=value.get("corporate_action_mode", "stop"),
        minimum_history_days=value.get("minimum_history_days", 252),
        annualisation_warning_days=value.get("annualisation_warning_days", 756),
        allow_zero_costs=value.get("allow_zero_costs", False),
    )
    return RunSettings(
        simulation=simulation,
        strategy_family=value["strategy_family"],
        momentum_lookback_days=value.get("momentum_lookback_days", 20),
        momentum_top_k=value.get("momentum_top_k", 3),
        momentum_rebalance_days=value.get("momentum_rebalance_days", 20),
        benchmark_tranches=value.get("benchmark_tranches", 1),
        window_size_days=value.get("window_size_days"),
    )


def load_synthetic_bars(path: Path) -> PriceData:
    """Local invented-bar JSON for tests and demonstrations only."""
    values = json.loads(path.read_text(encoding="utf-8"))
    return PriceData(
        (
            Bar(
                value["symbol"],
                date.fromisoformat(value["day"]),
                value["open_paise"],
                value["high_paise"],
                value["low_paise"],
                value["close_paise"],
                value["previous_close_paise"],
                value["volume"],
            )
            for value in values
        ),
        origin="synthetic",
    )


def _strategy(settings: RunSettings, decision_start: date | None) -> Strategy:
    if settings.strategy_family == "buy_and_hold":
        return buy_and_hold(settings.simulation, decision_start=decision_start)
    if settings.strategy_family == "momentum":
        return momentum_ranking(
            settings.simulation,
            lookback_days=settings.momentum_lookback_days,
            top_k=settings.momentum_top_k,
            rebalance_days=settings.momentum_rebalance_days,
            decision_start=decision_start,
        )
    raise ValueError("only buy_and_hold and momentum strategies are supported")


def evaluate_windows(
    data: PriceData, profile: CostProfile, settings: RunSettings
) -> tuple[WindowReport, ...]:
    """Fresh portfolio and strategy for each consecutive out-of-sample span."""
    if not data.days:
        raise ValueError("no data")
    size = settings.window_size_days or len(data.days)
    windows: list[WindowReport] = []
    for offset in range(0, len(data.days), size):
        start = data.days[offset]
        end = data.days[min(offset + size, len(data.days)) - 1]
        warmup = max(settings.momentum_lookback_days + 1, 2)
        first_available = data.days[max(0, offset - warmup)]
        decision_start = data.days[offset - 1] if offset else None
        window_data = data.through(end, first_day=first_available)
        report = evaluate(
            window_data,
            profile,
            settings.simulation,
            _strategy(settings, decision_start),
            strategy_family=settings.strategy_family,
            benchmark_tranches=settings.benchmark_tranches,
            trading_start=start,
            benchmark_decision_start=decision_start,
        )
        windows.append(WindowReport(start, end, report))
    return tuple(windows)


def _json_default(value: object) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, default=_json_default, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _code_revision(project_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _code_dirty(project_root: Path) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return bool(result.stdout.strip()) if result.returncode == 0 else None


def _increment_trial(directory: Path, family: str) -> int:
    """Serialize local counter updates across concurrent CLI processes."""
    with (directory / "trials.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        counter_path = directory / "trials.json"
        counts = json.loads(counter_path.read_text()) if counter_path.exists() else {}
        number = int(counts.get(family, 0)) + 1
        counts[family] = number
        temporary = directory / f".{uuid4().hex}.tmp"
        temporary.write_bytes(_canonical(counts))
        os.replace(temporary, counter_path)
        return number


def save_run(
    data: PriceData,
    profile: CostProfile,
    settings: RunSettings,
    directory: Path,
    project_root: Path,
) -> RunOutcome:
    """Evaluate first; then assign a trial number and save an atomic JSON record."""
    windows = evaluate_windows(data, profile, settings)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    trial = _increment_trial(directory, settings.strategy_family)
    bars = data.ordered_bars()
    coverage = {
        symbol: {
            "rows": len(symbol_bars),
            "first_date": symbol_bars[0].day.isoformat(),
            "last_date": symbol_bars[-1].day.isoformat(),
        }
        for symbol in settings.simulation.universe
        if (symbol_bars := [bar for bar in bars if bar.symbol == symbol])
    }
    record = {
        "run_id": uuid4().hex,
        "created_at_utc": datetime.now(timezone.utc),
        "trial_number": trial,
        "strategy_family": settings.strategy_family,
        "banner": windows[0].report.banner,
        "config_hash_sha256": hashlib.sha256(
            _canonical({"settings": settings, "cost_profile": profile})
        ).hexdigest(),
        "data_hash_sha256": hashlib.sha256(_canonical(bars)).hexdigest(),
        "data_coverage": coverage,
        "universe": settings.simulation.universe,
        "universe_as_of": settings.simulation.universe_as_of,
        "cost_profile": profile,
        "code_revision": _code_revision(project_root),
        "code_dirty": _code_dirty(project_root),
        "windows": windows,
        "warnings": tuple(
            dict.fromkeys(
                warning for window in windows for warning in window.report.warnings
            )
        ),
    }
    path = (
        directory
        / f"{settings.strategy_family}-trial-{trial:04d}-{record['run_id']}.json"
    )
    temporary = directory / f".{uuid4().hex}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(_canonical(record))
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)
    return RunOutcome(
        trial, settings.strategy_family, windows[0].report.banner, windows, path
    )
