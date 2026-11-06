"""Local operational commands for SQLite backup and guarded restore."""

import argparse
import json
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, get_settings
from app.modules.backtest.costs import CostProfile
from app.modules.backtest.data import load_stored_prices
from app.modules.backtest.run import (
    load_run_settings,
    load_synthetic_bars,
    save_run,
)
from app.modules.research import ml_cli
from app.modules.research.contracts import StrategyFamily, Timeframe
from app.modules.research.core import build_dataset
from app.modules.research.intraday import (
    build_intraday_dataset,
    chronological_intraday_split,
)
from app.modules.research.intraday_io import load_synthetic_intraday
from app.modules.research.io import load_synthetic_research
from app.modules.research.readiness import ReadinessPolicy
from app.modules.research.readiness_io import load_calendar_evidence
from app.modules.research.splits import chronological_split
from app.services.backup_service import BackupError, create_backup, restore_backup
from app.services.data_readiness_service import ReadinessError, assess_stored_readiness
from app.services.integrity_service import check_ledger_integrity
from app.services.price_import_service import PriceImportError, import_price_file
from app.services.upstox_historical_provider import (
    HttpResponse,
    UpstoxError,
    UpstoxHistoricalDataProvider,
    UpstoxProviderConfig,
)
from app.services.upstox_pilot_service import (
    PILOT_SYMBOLS,
    PilotError,
    download_official_nse_master,
    ingest_batch,
    prepare_reference_subset,
    validate_temporary_target,
    verify_mock_metadata,
    verify_official_metadata,
)
from app.services.upstox_reconciliation_service import reconcile_prices

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class _AnalyticsSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")
    upstox_analytics_token: SecretStr | None = None


def _analytics_token() -> str | None:
    secret = _AnalyticsSettings().upstox_analytics_token
    return secret.get_secret_value() if secret is not None else None


def main(argv: Sequence[str] | None = None) -> int:
    """Run one backup or restore command and return a process exit code."""
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    if arguments.command == "backtest":
        return _run_backtest(arguments)
    if arguments.command == "pilot-upstox":
        return _run_upstox_pilot(arguments)
    if arguments.command == "data-readiness":
        return _run_data_readiness(arguments)
    if arguments.command == "research-validate":
        return _run_research_validate(arguments)
    if arguments.command == "research-intraday-validate":
        return _run_research_intraday_validate(arguments)
    if arguments.command in ml_cli.COMMANDS:
        return ml_cli.run_ml_cli(arguments)
    try:
        settings = get_settings()
        database_path = _database_path(arguments.database, settings.database_url)
        if arguments.command == "integrity":
            integrity_result = check_ledger_integrity(database_path)
            if integrity_result.is_valid:
                print("Ledger integrity check passed.")
                return 0
            print("Ledger integrity check failed:", file=sys.stderr)
            for discrepancy in integrity_result.discrepancies:
                print(f"- {discrepancy}", file=sys.stderr)
            return 1

        if arguments.command == "import-prices":
            if not database_path.is_file():
                raise PriceImportError(
                    "database file does not exist; run migrations first"
                )
            database_engine = create_engine(f"sqlite:///{database_path}")

            @event.listens_for(database_engine, "connect")
            def enable_foreign_keys(connection: object, _: object) -> None:
                cursor = connection.cursor()  # type: ignore[attr-defined]
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            try:
                with Session(database_engine) as session:
                    result = import_price_file(
                        session,
                        arguments.file,
                        arguments.source_label,
                        max_uncompressed_bytes=settings.max_import_uncompressed_mb
                        * 1024
                        * 1024,
                        skip_bad_rows=arguments.skip_bad_rows,
                    )
            finally:
                database_engine.dispose()
            print(f"SHA-256: {result.file_sha256}")
            print(f"Already imported: {result.already_imported}")
            print(
                f"Rows: total={result.total_rows}, imported={result.imported_rows}, "
                f"skipped={result.skipped_rows}, rejected={len(result.rejected_rows)}"
            )
            for series, count in sorted(result.skipped_by_series.items()):
                print(f"Skipped series {series}: {count}")
            for rejected in result.rejected_rows:
                print(f"Rejected line {rejected.line}: {rejected.reason}")
            return 0

        backup_directory = (
            arguments.backup_directory or settings.backup_directory
        ).expanduser()
        retention_count = (
            arguments.retention
            if arguments.retention is not None
            else settings.backup_retention_count
        )
        if arguments.command == "backup":
            backup_result = create_backup(
                database_path, backup_directory, retention_count
            )
            print(f"Backup created: {backup_result.path}")
            print(f"Older backups pruned: {len(backup_result.pruned_paths)}")
            return 0
        if arguments.command == "restore":
            restore_result = restore_backup(
                arguments.backup,
                database_path,
                backup_directory,
                retention_count,
                _known_migration_revisions(),
                confirm=arguments.confirm,
            )
            print(f"Database restored from: {restore_result.restored_from}")
            print(f"Pre-restore safety backup: {restore_result.safety_backup_path}")
            return 0
    except (BackupError, PriceImportError, SQLAlchemyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    parser.error("a command is required")
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Investment Agent database tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backup_parser = subparsers.add_parser(
        "backup", help="Create a consistent SQLite backup"
    )
    _add_common_options(backup_parser)

    integrity_parser = subparsers.add_parser(
        "integrity", help="Verify ledger reconciliation without writing"
    )
    _add_database_option(integrity_parser)

    import_parser = subparsers.add_parser(
        "import-prices", help="Import one manually downloaded local bhavcopy ZIP"
    )
    import_parser.add_argument("file", type=Path, help="Local ZIP file")
    import_parser.add_argument(
        "--source-label", required=True, help="Human-readable data source label"
    )
    import_parser.add_argument(
        "--skip-bad-rows", action="store_true", help="Report and skip invalid EQ rows"
    )
    _add_database_option(import_parser)

    restore_parser = subparsers.add_parser(
        "restore", help="Restore a verified SQLite backup"
    )
    restore_parser.add_argument("backup", type=Path, help="Backup database to restore")
    restore_parser.add_argument(
        "--confirm",
        action="store_true",
        help="Confirm replacement of the current database",
    )
    _add_common_options(restore_parser)

    backtest_parser = subparsers.add_parser(
        "backtest", help="Run a local, daily-bar historical simulation"
    )
    backtest_parser.add_argument("--config", type=Path, required=True)
    backtest_parser.add_argument("--cost-profile", type=Path, required=True)
    source = backtest_parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--database",
        type=Path,
        help="Explicit stored-price SQLite file, opened read-only",
    )
    source.add_argument(
        "--synthetic-bars", type=Path, help="Local JSON of invented bars for tests"
    )
    backtest_parser.add_argument(
        "--runs-directory", type=Path, default=PROJECT_ROOT / "backtest-runs"
    )

    pilot_parser = subparsers.add_parser(
        "pilot-upstox", help="Bounded five-stock Upstox reconciliation pilot"
    )
    pilot_parser.add_argument("--mode", choices=("offline", "live"), required=True)
    pilot_parser.add_argument("--reference-database", type=Path, required=True)
    pilot_parser.add_argument("--database", type=Path, required=True)
    pilot_parser.add_argument("--mock-file", type=Path)
    pilot_parser.add_argument(
        "--symbols",
        nargs=5,
        default=PILOT_SYMBOLS,
        metavar="SYMBOL",
        help="Exactly five EQ symbols; defaults to the five-stock pilot",
    )
    pilot_parser.add_argument("--allow-live", action="store_true")
    readiness_parser = subparsers.add_parser(
        "data-readiness", help="Read-only historical data eligibility report"
    )
    readiness_parser.add_argument("--database", type=Path, required=True)
    readiness_parser.add_argument(
        "--strategy",
        choices=tuple(item.value for item in StrategyFamily),
        required=True,
    )
    readiness_parser.add_argument(
        "--timeframe",
        choices=tuple(item.value for item in Timeframe),
        required=True,
    )
    readiness_parser.add_argument("--calendar-json", type=Path)
    readiness_parser.add_argument("--min-sessions", type=int)
    readiness_parser.add_argument("--symbols", nargs="*", default=[])
    readiness_parser.add_argument("--provider")
    research_parser = subparsers.add_parser(
        "research-validate", help="Validate a synthetic point-in-time research dataset"
    )
    research_parser.add_argument("--synthetic-json", type=Path, required=True)
    research_parser.add_argument("--train-end", type=date.fromisoformat)
    research_parser.add_argument("--validation-end", type=date.fromisoformat)
    research_parser.add_argument("--embargo-sessions", type=int, default=0)
    intraday_parser = subparsers.add_parser(
        "research-intraday-validate",
        help="Validate synthetic intraday research data without database access",
    )
    intraday_parser.add_argument("--synthetic-json", type=Path, required=True)
    intraday_parser.add_argument("--train-end-utc", type=datetime.fromisoformat)
    intraday_parser.add_argument("--validation-end-utc", type=datetime.fromisoformat)
    intraday_parser.add_argument("--embargo-minutes", type=int, default=0)
    ml_cli.add_ml_parsers(subparsers)
    return parser


def _run_research_validate(arguments: argparse.Namespace) -> int:
    """Do not load application settings, credentials, or the real database."""
    try:
        observations, config = load_synthetic_research(arguments.synthetic_json)
        dataset = build_dataset(observations, config)
        report = dataset.report
        print(
            f"Instruments: {report.instruments}; candidates: "
            f"{report.candidate_observations}; usable: {report.usable_observations}"
        )
        print(f"Coverage: {report.first_date} to {report.last_date}")
        print(
            f"Missing features: {json.dumps(report.missing_features, sort_keys=True)}"
        )
        print(
            f"Invalid labels: {report.invalid_labels}; action/adjustment blockers: "
            f"{report.corporate_action_or_adjustment_blockers}; unknown availability: "
            f"{report.unknown_historical_availability}"
        )
        print(
            f"Duplicate observations: {report.duplicate_observations}; "
            f"conflicting observations: {report.conflicting_observations}"
        )
        print(
            f"Rejected reasons: {json.dumps(report.rejected_reasons, sort_keys=True)}"
        )
        if (arguments.train_end is None) != (arguments.validation_end is None):
            raise ValueError("both chronological split boundaries are required")
        if arguments.train_end is not None:
            split = chronological_split(
                dataset.samples,
                config.sessions,
                arguments.train_end,
                arguments.validation_end,
                embargo_sessions=arguments.embargo_sessions,
            )
            print(
                f"Split: train={len(split.train)}, "
                f"validation={len(split.validation)}, test={len(split.test)}, "
                f"excluded={len(split.excluded)}"
            )
        unsafe = (
            not report.usable_observations
            or report.duplicate_observations
            or report.conflicting_observations
            or report.corporate_action_or_adjustment_blockers
            or report.unknown_historical_availability
        )
        return 1 if unsafe else 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Research validation error: {exc}", file=sys.stderr)
        return 1


def _run_research_intraday_validate(arguments: argparse.Namespace) -> int:
    """Synthetic fixtures only; this branch never loads settings or credentials."""
    try:
        candles, config = load_synthetic_intraday(arguments.synthetic_json)
        dataset = build_intraday_dataset(candles, config)
        report = dataset.report
        print(
            f"Synthetic intraday {config.timeframe.value}: instruments="
            f"{report.instruments}, bars={report.input_bars}, "
            f"candidates={report.candidates}, accepted={report.accepted}, "
            f"rejected={report.rejected}"
        )
        print(
            f"Duplicates={report.duplicate_timestamps}, "
            f"out-of-order={report.out_of_order_observations}, "
            f"missing bars={report.missing_expected_bars}, "
            f"missing targets={report.missing_targets}"
        )
        print(f"Reasons: {json.dumps(report.rejection_reasons, sort_keys=True)}")
        if (arguments.train_end_utc is None) != (arguments.validation_end_utc is None):
            raise ValueError("both chronological split boundaries are required")
        if arguments.train_end_utc is not None:
            split = chronological_intraday_split(
                dataset.samples,
                arguments.train_end_utc,
                arguments.validation_end_utc,
                embargo=timedelta(minutes=arguments.embargo_minutes),
            )
            print(
                f"Split: train={len(split.train)}, validation={len(split.validation)}, "
                f"test={len(split.test)}, excluded={len(split.excluded)}"
            )
        return (
            0
            if report.accepted
            and not (
                report.duplicate_timestamps
                or report.out_of_order_observations
                or report.missing_expected_bars
            )
            else 1
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Intraday research validation error: {exc}", file=sys.stderr)
        return 1


def _run_backtest(arguments: argparse.Namespace) -> int:
    """A separate branch avoids loading application settings or the user's .env."""
    try:
        settings = load_run_settings(arguments.config)
        profile = CostProfile.from_json(arguments.cost_profile)
        if arguments.database is not None:
            if not profile.verified:
                raise ValueError("stored prices require a verified broker cost profile")
            data = load_stored_prices(arguments.database, settings.simulation.universe)
        else:
            data = load_synthetic_bars(arguments.synthetic_bars)
        outcome = save_run(
            data, profile, settings, arguments.runs_directory, PROJECT_ROOT
        )
    except (
        ValueError,
        FileNotFoundError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
        SQLAlchemyError,
    ) as exc:
        print(f"Backtest error: {exc}", file=sys.stderr)
        return 1
    print(outcome.banner)
    print(f"Strategy: {outcome.strategy_family}; trial #{outcome.trial_number}")
    for index, window in enumerate(outcome.windows, 1):
        result = window.report.metrics
        print(
            f"Trial #{outcome.trial_number}, window {index}: "
            f"{window.start} to {window.end}; "
            f"{result.trading_days} trading days; "
            f"gross {result.gross_return_percent:.2f}%; "
            f"net {result.net_return_percent:.2f}%"
        )
        print(
            f"  Final net equity {result.final_net_equity_paise} paise; "
            f"charges {result.total_costs_paise} paise; "
            f"slippage impact {result.slippage_impact_paise} paise; "
            f"cost drag {result.cost_drag_paise} paise"
        )
        print(
            f"  Max drawdown {result.max_drawdown_percent:.2f}%; "
            f"daily volatility {result.daily_volatility_percent:.2f}%; "
            f"mean exposure {result.mean_exposure_percent:.2f}%"
        )
        annual_return = (
            f"{result.annualized_return_percent:.2f}%"
            if result.annualized_return_percent is not None
            else "unavailable"
        )
        annual_volatility = (
            f"{result.annualized_volatility_percent:.2f}%"
            if result.annualized_volatility_percent is not None
            else "unavailable"
        )
        print(
            f"  Annualised return {annual_return}; "
            f"annualised volatility {annual_volatility}"
        )
        print(
            f"  Trades {result.trade_count}; turnover {result.turnover_paise} paise "
            f"({result.turnover_percent:.2f}%); "
            f"rejected {result.rejected_trade_count}; "
            f"volume-cap reduced {result.reduced_quantity}, "
            f"unfilled {result.unfilled_quantity}; "
            f"Profit Vault {result.profit_vault_paise} paise"
        )
        if result.rejection_reasons:
            print(
                "  Rejection reasons: "
                + ", ".join(
                    f"{reason}={count}" for reason, count in result.rejection_reasons
                )
            )
        for benchmark in window.report.benchmarks:
            print(
                f"  Trial #{outcome.trial_number}, {benchmark.label}: "
                f"gross {benchmark.metrics.gross_return_percent:.2f}%; "
                f"net {benchmark.metrics.net_return_percent:.2f}%; "
                f"charges {benchmark.metrics.total_costs_paise} paise"
            )
        for warning in window.report.warnings:
            print(f"  Warning: {warning}")
    print(f"Run record: {outcome.record_path}")
    return 0


class _NoNetworkTransport:
    """Make a mistaken fetch in the offline pilot fail before opening a socket."""

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> HttpResponse:
        raise PilotError("offline pilot cannot make a network request")


def _load_mock_fixture(
    path: Path,
    symbols: tuple[str, ...],
) -> tuple[dict[str, dict[str, object]], dict[str, bytes]]:
    """Accept only the five explicit, labelled mock identities and responses."""
    if not path.is_file() or path.stat().st_size > 2_000_000:
        raise PilotError("mock fixture is missing or exceeds 2 MB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PilotError("mock fixture is not valid UTF-8 JSON") from None
    if not isinstance(payload, dict) or payload.get("mode") != "synthetic_mock":
        raise PilotError("mock fixture must be labelled synthetic_mock")
    raw_instruments = payload.get("instruments")
    raw_responses = payload.get("responses")
    if not isinstance(raw_instruments, list) or not isinstance(raw_responses, dict):
        raise PilotError("mock fixture requires instruments and responses")
    metadata: dict[str, dict[str, object]] = {}
    for record in raw_instruments:
        if not isinstance(record, dict):
            raise PilotError("mock instrument metadata must be an object")
        symbol = record.get("trading_symbol")
        if not isinstance(symbol, str) or symbol in metadata:
            raise PilotError("mock instrument symbols are missing or duplicated")
        metadata[symbol] = record
    if set(metadata) != set(symbols) or set(raw_responses) != set(symbols):
        raise PilotError("mock fixture must contain exactly the five pilot symbols")
    responses: dict[str, bytes] = {}
    for symbol in symbols:
        response = raw_responses[symbol]
        if not isinstance(response, dict):
            raise PilotError(f"{symbol} mock response must be an object")
        responses[symbol] = json.dumps(
            response, separators=(",", ":"), default=str
        ).encode("utf-8")
    return metadata, responses


def _run_upstox_pilot(arguments: argparse.Namespace) -> int:
    """Run five bounded historical requests or an offline fixture comparison."""
    request_count = 0
    try:
        target = validate_temporary_target(arguments.database)
        live = arguments.mode == "live"
        symbols = tuple(arguments.symbols)
        if live:
            if not arguments.allow_live:
                raise PilotError("live mode requires --allow-live")
            if symbols != PILOT_SYMBOLS:
                raise PilotError("live mode is limited to the five approved symbols")
            if arguments.mock_file is not None:
                raise PilotError("live mode cannot use --mock-file")
            token = _analytics_token()
            if not token:
                raise PilotError("live mode requires UPSTOX_ANALYTICS_TOKEN")
        elif arguments.allow_live:
            raise PilotError("--allow-live cannot be used in offline mode")
        if not live:
            if arguments.mock_file is None:
                raise PilotError("offline mode requires --mock-file")
            metadata, responses = _load_mock_fixture(arguments.mock_file, symbols)
        reference = prepare_reference_subset(
            arguments.reference_database, target, symbols
        )
        if live:
            metadata = download_official_nse_master(reference)
        engine = create_engine(f"sqlite:///{reference.database_path}")

        @event.listens_for(engine, "connect")
        def enable_pilot_foreign_keys(connection: object, _: object) -> None:
            cursor = connection.cursor()  # type: ignore[attr-defined]
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        try:
            provider = (
                UpstoxHistoricalDataProvider(
                    UpstoxProviderConfig(max_attempts=1, timeout_seconds=10),
                    token_getter=lambda: token,
                )
                if live
                else UpstoxHistoricalDataProvider(transport=_NoNetworkTransport())
            )
            batches = {}
            for symbol in symbols:
                with Session(engine) as session:
                    mapping = (
                        verify_official_metadata(
                            session, metadata[symbol], reference, symbol
                        )
                        if live
                        else verify_mock_metadata(
                            session, metadata[symbol], reference, symbol
                        )
                    )
                    instrument_key = mapping.provider_instrument_key
                if live:
                    if request_count:
                        time.sleep(1.1)
                    request_count += 1
                    batch = provider.fetch_daily(
                        instrument_key or "",
                        reference.from_date,
                        reference.to_date,
                    )
                else:
                    batch = provider.parse_response(
                        responses[symbol],
                        instrument_key or "",
                        reference.from_date,
                        reference.to_date,
                        retrieved_at_utc=datetime.now(UTC),
                        source_ref=f"offline-mock:{symbol}",
                    )
                batches[symbol] = batch
                with Session(engine) as session:
                    created = ingest_batch(session, batch, symbol, reference)
                print(
                    f"{symbol}: {'real' if live else 'mocked'} observations "
                    f"added={created}"
                )
            repeated = 0
            for symbol, batch in batches.items():
                with Session(engine) as session:
                    repeated += ingest_batch(session, batch, symbol, reference)
            with Session(engine) as session:
                report = reconcile_prices(
                    session, reference.instrument_ids, reference.dates
                )
            integrity = check_ledger_integrity(reference.database_path)
            if not integrity.is_valid:
                raise PilotError(
                    "temporary pilot failed integrity checks: "
                    + "; ".join(integrity.discrepancies)
                )
        finally:
            engine.dispose()
        if live:
            print(f"LIVE UPSTOX PILOT — historical GET requests={request_count}")
        else:
            print(
                "OFFLINE MOCK PILOT — synthetic provider responses; no Upstox request"
            )
        print(
            f"NSE reference: {len(reference.instruments)} EQ instruments, "
            f"{len(reference.dates)} sessions "
            f"({reference.from_date}..{reference.to_date}), "
            f"{reference.expected_nse_rows} canonical observations"
        )
        print(f"Identical redelivery added={repeated} observations")
        for item in report.symbols:
            counts = item.counts
            print(
                f"{item.symbol} {'official' if live else 'mock'}-identity-"
                f"matched-ISIN={item.isin}: "
                f"NSE={counts.nse_rows}, "
                f"{'Upstox' if live else 'mock'}={counts.upstox_rows}, "
                f"exact={counts.exact_matches}, "
                f"OHLC-match={counts.exact_ohlc_matches}, "
                f"close-match={counts.close_matches}, "
                f"price-diff={counts.price_disagreements}, "
                f"volume-diff={counts.volume_disagreements}, "
                f"missing-NSE={counts.missing_nse}, "
                f"missing-{'Upstox' if live else 'mock'}="
                f"{counts.missing_upstox}, "
                f"revisions={counts.revision_ambiguous}"
            )
            for row in item.rows:
                if row.state != "EXACT_MATCH":
                    differences = ", ".join(
                        f"{field.field}: NSE={field.nse_value}, "
                        f"{'Upstox' if live else 'mock'}={field.upstox_value}, "
                        f"delta={field.difference:+d}"
                        for field in row.differences
                    )
                    print(
                        f"  {row.trade_date} {row.state}"
                        + (f" ({differences})" if differences else "")
                    )
        total = report.aggregate
        print(
            "Aggregate: "
            f"NSE={total.nse_rows}, "
            f"{'Upstox' if live else 'mock'}={total.upstox_rows}, "
            f"exact={total.exact_matches}, "
            f"OHLC-match={total.exact_ohlc_matches}, "
            f"close-match={total.close_matches}, "
            f"price-diff={total.price_disagreements}, "
            f"volume-diff={total.volume_disagreements}, "
            f"missing-NSE={total.missing_nse}, "
            f"missing-mock={total.missing_upstox}, "
            f"revisions={total.revision_ambiguous}"
        )
        print(f"Temporary pilot database: {reference.database_path}")
        print("Temporary database integrity: passed")
        return 0
    except (PilotError, UpstoxError, ValueError, SQLAlchemyError, OSError) as exc:
        if arguments.mode == "live":
            print(f"Historical GET attempts: {request_count}", file=sys.stderr)
        print(f"Upstox pilot error: {exc}", file=sys.stderr)
        return 1


def _run_data_readiness(arguments: argparse.Namespace) -> int:
    """Report stored evidence without reading credentials or writing SQLite."""
    try:
        strategy = StrategyFamily(arguments.strategy)
        timeframe = Timeframe(arguments.timeframe)
        minimum = arguments.min_sessions
        if minimum is None:
            minimum = 756 if strategy is StrategyFamily.LONG_TERM else 504
        calendar = (
            load_calendar_evidence(arguments.calendar_json)
            if arguments.calendar_json is not None
            else None
        )
        policy = ReadinessPolicy(
            strategy,
            timeframe,
            minimum,
            calendar,
            provider_access_blocked=arguments.provider == "upstox",
            as_of_date=datetime.now(ZoneInfo("Asia/Kolkata")).date(),
        )
        report = assess_stored_readiness(
            arguments.database,
            policy,
            symbols=tuple(arguments.symbols),
            provider=arguments.provider,
        )
        print(json.dumps(report.as_dict(), indent=2))
        return 0 if report.eligible else 1
    except (
        ReadinessError,
        ValueError,
        TypeError,
        OSError,
        json.JSONDecodeError,
    ) as exc:
        print(f"Readiness error: {exc}", file=sys.stderr)
        return 2


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    _add_database_option(parser)
    parser.add_argument(
        "--backup-directory",
        type=Path,
        help="Backup destination; defaults to BACKUP_DIRECTORY",
    )
    parser.add_argument(
        "--retention",
        type=int,
        help="Number of newest backups to retain",
    )


def _add_database_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--database",
        type=Path,
        help="SQLite database path; defaults to DATABASE_URL",
    )


def _database_path(override: Path | None, database_url: str) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        raise BackupError("backup and restore require a file-backed SQLite database")
    if url.database == ":memory:":
        raise BackupError("backup and restore do not support in-memory SQLite")
    path = Path(url.database).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def _known_migration_revisions() -> frozenset[str]:
    config = Config()
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    scripts = ScriptDirectory.from_config(config)
    return frozenset(
        revision.revision
        for revision in scripts.walk_revisions(base="base", head="heads")
    )


if __name__ == "__main__":
    raise SystemExit(main())
