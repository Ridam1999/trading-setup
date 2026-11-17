"""Command-line entry point for the research engine."""

from __future__ import annotations

import hashlib
import html
import itertools
import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

import duckdb
import pandas as pd  # type: ignore[import-untyped]
import typer
import yaml

from nse_engine.backtest.config import load_backtest_settings
from nse_engine.backtest.costs import load_costs_config
from nse_engine.backtest.engine import run_portfolio_backtest
from nse_engine.backtest.metrics import portfolio_metrics
from nse_engine.backtest.portfolio import cost_stress_runs, monte_carlo_drawdowns
from nse_engine.backtest.report import write_backtest_report
from nse_engine.backtest.walkforward import walk_forward_backtest
from nse_engine.calendar import build_calendar
from nse_engine.config import (
    DataConfig,
    config_directory,
    load_config,
    load_data_bias_status,
    load_data_config,
)
from nse_engine.data.adjust import attach_raw_close
from nse_engine.data.corporate_actions import (
    apply_corporate_actions,
    load_corporate_actions,
    validate_adjustments,
)
from nse_engine.data.quality import QualityReport, check_ohlcv, write_health_report
from nse_engine.data.sources.eod2_import import EOD2Source
from nse_engine.data.sources.nselib_source import BhavcopySource, download_bhavcopy
from nse_engine.data.sources.pit_history import PitHistorySource
from nse_engine.data.store import MarketDataStore
from nse_engine.data.universe import (
    liquidity_universe_settings,
    load_pit_membership,
    load_universe,
    point_in_time_liquidity_membership,
)
from nse_engine.factor.acquire import (
    DISCLAIMER as ACQUIRE_DISCLAIMER,
)
from nse_engine.factor.acquire import (
    build_membership,
    download_announcements,
    download_bhavcopies,
    download_filing_pdfs,
)
from nse_engine.factor.config import (
    AcquisitionConfig,
    FactorDataConfig,
    FundamentalExtractionConfig,
    HypothesisId,
    load_factor_config,
    load_factor_data_config,
)
from nse_engine.factor.fundamentals import parse_filings
from nse_engine.factor.readiness import DISCLAIMER as FACTOR_DISCLAIMER
from nse_engine.factor.readiness import assess_readiness
from nse_engine.factor.report import PARTIAL_STUDY_BANNER, write_factor_report
from nse_engine.factor.study import DISCLAIMER as FACTOR_STUDY_DISCLAIMER
from nse_engine.factor.study import run_factor_study
from nse_engine.logging_config import configure_logging
from nse_engine.paper.config import load_approval_entries, load_paper_settings
from nse_engine.paper.ledger import PaperLedger
from nse_engine.paper.report import DISCLAIMER as PAPER_DISCLAIMER
from nse_engine.paper.runner import run_paper_day
from nse_engine.setups.base import Setup, SetupConfig, load_setup_config
from nse_engine.setups.registry import create_setup
from nse_engine.study.config import load_gate_settings, load_study_settings
from nse_engine.study.control import LookAheadControl, summarize_control_results
from nse_engine.study.events import build_event_study
from nse_engine.study.gates import evaluate_provisional_gates, evaluate_setup_gates
from nse_engine.study.splits import SplitName, configured_split, resolve_split
from nse_engine.study.stats import (
    benjamini_hochberg,
    block_bootstrap_expectancy,
    return_summary,
    symbol_breadth,
    yearly_returns,
)
from nse_engine.study.trials import TrialRegistry

app = typer.Typer(help="Local NSE setup research tools.")
data_app = typer.Typer(help="Import and inspect daily market data.")
study_app = typer.Typer(help="Run and review rule-based event studies.")
backtest_app = typer.Typer(help="Run daily portfolio simulations and walk-forward tests.")
paper_app = typer.Typer(help="Run the daily paper-trading scanner and ledger.")
factor_app = typer.Typer(help="Check readiness and run the frozen factor study; never approves.")
acquire_app = typer.Typer(help="Download public PIT inputs without credentials or live orders.")
logger = logging.getLogger(__name__)
PROVISIONAL_BANNER = "SURVIVOR_BIASED: PROVISIONAL KILL STAGE; NOT APPROVAL EVIDENCE"
app.add_typer(data_app, name="data")
app.add_typer(study_app, name="study")
app.add_typer(backtest_app, name="backtest")
app.add_typer(paper_app, name="paper")
app.add_typer(factor_app, name="factor")
factor_app.add_typer(acquire_app, name="acquire")


@app.callback()
def main() -> None:
    """Set up logging before running a command."""
    configure_logging()


@app.command("config-check")
def config_check(
    directory: Annotated[
        Path | None, typer.Option("--directory", help="YAML config directory.")
    ] = None,
) -> None:
    """Validate YAML configuration and print a redacted summary."""
    try:
        config = load_config(directory)
    except (FileNotFoundError, ValueError) as error:
        logger.error("Configuration validation failed: %s", error)
        raise typer.Exit(code=1) from error
    typer.echo(
        json.dumps(
            {
                "app_name": config.base.app_name,
                "timezone": config.base.timezone,
                "log_level": config.base.log_level,
                "random_seed": config.base.random_seed,
                "documents": sorted(config.documents),
            },
            indent=2,
        )
    )


@factor_app.command("check")
def factor_check(
    directory: Annotated[
        Path | None, typer.Option("--directory", help="YAML config directory.")
    ] = None,
    prereg: Annotated[
        Path, typer.Option("--prereg", help="Frozen pre-registration document.")
    ] = Path("docs/FACTOR_STUDY_PREREG.md"),
    hypothesis: Annotated[
        HypothesisId | None,
        typer.Option("--hypothesis", help="Check one frozen hypothesis only (partial study)."),
    ] = None,
) -> None:
    """Report whether PIT inputs and safety conditions permit a factor study."""
    config = load_factor_config(directory)
    data = load_factor_data_config(directory)
    selected_ids = [hypothesis] if hypothesis is not None else None
    report = assess_readiness(config, data, prereg, hypothesis_ids=selected_ids)
    scope = hypothesis if hypothesis is not None else "H1,H2,H3,H4"
    typer.echo(f"STUDY SCOPE: {scope}" + (" (PARTIAL)" if hypothesis is not None else " (FULL)"))
    for check in report.checks:
        state = "OK" if check.ok else "BLOCK"
        typer.echo(f"[{state}] {check.name}: {check.detail}")
    if hypothesis is not None:
        typer.echo(PARTIAL_STUDY_BANNER)
    typer.echo(FACTOR_DISCLAIMER)
    if report.ready:
        typer.echo("FACTOR STUDY READY: design frozen and PIT inputs present.")
        return
    typer.echo(f"FACTOR STUDY BLOCKED: {', '.join(report.blockers)}", err=True)
    raise typer.Exit(code=1)


@factor_app.command("run")
def factor_run(
    directory: Annotated[
        Path | None, typer.Option("--directory", help="YAML config directory.")
    ] = None,
    prereg: Annotated[
        Path, typer.Option("--prereg", help="Frozen pre-registration document.")
    ] = Path("docs/FACTOR_STUDY_PREREG.md"),
    unlock_holdout: Annotated[
        bool,
        typer.Option(
            "--unlock-holdout",
            help="Explicitly record and read the locked holdout period.",
        ),
    ] = False,
    hypothesis: Annotated[
        HypothesisId | None,
        typer.Option("--hypothesis", help="Run one frozen hypothesis only (partial study)."),
    ] = None,
) -> None:
    """Run the frozen study; every result is a candidate, never an approval."""
    factor = load_factor_config(directory)
    data = load_factor_data_config(directory)
    settings = load_study_settings(directory)
    gate_settings = load_gate_settings(directory)
    costs = load_costs_config(directory)
    registry = TrialRegistry(settings.trial_registry_path)
    selected_ids = [hypothesis] if hypothesis is not None else None
    readiness = assess_readiness(factor, data, prereg, registry, hypothesis_ids=selected_ids)
    if not readiness.ready:
        for check in readiness.checks:
            if not check.ok:
                typer.echo(f"[BLOCK] {check.name}: {check.detail}", err=True)
        typer.echo(f"FACTOR STUDY BLOCKED: {', '.join(readiness.blockers)}", err=True)
        typer.echo(FACTOR_DISCLAIMER)
        raise typer.Exit(code=1)
    if hypothesis is not None:
        typer.echo(PARTIAL_STUDY_BANNER, err=True)
    if unlock_holdout:
        typer.echo("HOLDOUT ACCESS REQUESTED; an audit row will be appended.", err=True)

    output = run_factor_study(
        factor,
        data,
        settings,
        gate_settings,
        costs,
        registry,
        prereg,
        config_directory=directory,
        include_holdout=unlock_holdout,
        hypothesis_ids=selected_ids,
    )
    report_path = data.report_path
    if hypothesis is not None:
        report_path = report_path.with_name(
            f"{report_path.stem}_{hypothesis.lower()}{report_path.suffix}"
        )
    written_report = write_factor_report(
        output,
        report_path,
        load_data_bias_status(load_config(directory)),
        factor.prereg_sha256,
        frozen_ok=True,
        holdout_accessed=unlock_holdout,
    )
    typer.echo(
        json.dumps(
            {
                "hypotheses": list(output.hypotheses),
                "complete_frozen_design": output.complete_design,
                "results": len(output.results),
                "trials_appended": output.trials_appended,
                "candidates": [
                    f"{row.hypothesis_id}:{row.horizon_months}mo"
                    for row in output.results
                    if row.candidate
                ],
                "report_path": str(written_report),
                "holdout_accessed": unlock_holdout,
            },
            indent=2,
        )
    )
    typer.echo("SURVIVOR_BIASED: FACTOR RESULTS ARE CANDIDATES ONLY; NOT APPROVAL EVIDENCE")
    typer.echo(FACTOR_STUDY_DISCLAIMER)


def _require_acquisition(data: FactorDataConfig) -> AcquisitionConfig:
    """Return the public acquisition settings or fail with a clear message."""
    if data.acquisition is None:
        typer.echo("Acquisition settings are missing from config/factor_data.yaml.", err=True)
        raise typer.Exit(code=1)
    return data.acquisition


def _require_fundamentals(data: FactorDataConfig) -> FundamentalExtractionConfig:
    """Return the configured extraction rules or fail before parsing files."""
    if data.fundamentals is None:
        typer.echo(
            "Fundamental extraction settings are missing from config/factor_data.yaml.", err=True
        )
        raise typer.Exit(code=1)
    return data.fundamentals


def _require_data_path(value: Path | None, label: str) -> Path:
    """Return a configured data path or fail before any network call."""
    if value is None:
        typer.echo(f"Missing configured path: {label}", err=True)
        raise typer.Exit(code=1)
    return value


@acquire_app.command("prices")
def acquire_prices(
    directory: Annotated[
        Path | None, typer.Option("--directory", help="YAML config directory.")
    ] = None,
) -> None:
    """Download missing dated NSE bhavcopies into the PIT price store."""
    data = load_factor_data_config(directory)
    settings = _require_acquisition(data)
    destination = _require_data_path(data.pit_prices_dir, "pit_prices_dir")
    summary = download_bhavcopies(settings, destination)
    typer.echo(
        json.dumps(
            {
                "downloaded": [day.isoformat() for day in summary.downloaded],
                "skipped": [day.isoformat() for day in summary.skipped],
                "preserved": [day.isoformat() for day in summary.preserved],
            },
            indent=2,
        )
    )
    typer.echo(ACQUIRE_DISCLAIMER)


@acquire_app.command("membership")
def acquire_membership(
    directory: Annotated[
        Path | None, typer.Option("--directory", help="YAML config directory.")
    ] = None,
) -> None:
    """Build PIT top-N membership snapshots from downloaded bhavcopies."""
    factor = load_factor_config(directory)
    data = load_factor_data_config(directory)
    settings = _require_acquisition(data)
    price_dir = _require_data_path(data.pit_prices_dir, "pit_prices_dir")
    output_path = _require_data_path(data.membership_path, "membership_path")
    built = build_membership(factor, settings, price_dir, output_path)
    typer.echo(json.dumps({"membership_path": str(built)}, indent=2))
    typer.echo(ACQUIRE_DISCLAIMER)


@acquire_app.command("announcements")
def acquire_announcements(
    directory: Annotated[
        Path | None, typer.Option("--directory", help="YAML config directory.")
    ] = None,
) -> None:
    """Download public announcement metadata into an appendable Parquet index."""
    data = load_factor_data_config(directory)
    settings = _require_acquisition(data)
    output_path = _require_data_path(data.announcement_index_path, "announcement_index_path")
    summary = download_announcements(settings, output_path)
    typer.echo(
        json.dumps(
            {
                "chunks": summary.chunks,
                "rows": summary.rows,
                "output_path": str(summary.output_path),
            },
            indent=2,
        )
    )
    typer.echo(ACQUIRE_DISCLAIMER)


@acquire_app.command("filings")
def acquire_filings(
    directory: Annotated[
        Path | None, typer.Option("--directory", help="YAML config directory.")
    ] = None,
) -> None:
    """Download linked filing PDFs listed in the announcement index."""
    data = load_factor_data_config(directory)
    settings = _require_acquisition(data)
    index_path = _require_data_path(data.announcement_index_path, "announcement_index_path")
    saved = download_filing_pdfs(index_path, settings)
    typer.echo(json.dumps({"saved": saved, "filing_dir": str(settings.filing_dir)}, indent=2))
    typer.echo(ACQUIRE_DISCLAIMER)


@acquire_app.command("fundamentals")
def acquire_fundamentals(
    directory: Annotated[
        Path | None, typer.Option("--directory", help="YAML config directory.")
    ] = None,
) -> None:
    """Append announcement-dated fundamentals parsed from downloaded filings."""
    data = load_factor_data_config(directory)
    settings = _require_acquisition(data)
    rules = _require_fundamentals(data)
    index_path = _require_data_path(data.announcement_index_path, "announcement_index_path")
    output_path = _require_data_path(data.fundamentals_path, "fundamentals_path")
    summary = parse_filings(index_path, settings, rules, output_path)
    typer.echo(
        json.dumps(
            {
                "files_read": summary.files_read,
                "rows_added": summary.rows_added,
                "rows_already_present": summary.rows_already_present,
                "missing_files": summary.missing_files,
                "unparsed_files": summary.unparsed_files,
                "output_path": str(summary.output_path),
            },
            indent=2,
        )
    )
    typer.echo(ACQUIRE_DISCLAIMER)


def _guard_provisional_split(split: SplitName, provisional: bool) -> None:
    """Block holdout access in kill-stage mode before any registry access."""
    if provisional and split == "holdout":
        typer.echo("Provisional studies are train/validation only; holdout is locked.", err=True)
        raise typer.Exit(code=2)


def _store() -> tuple[MarketDataStore, DataConfig]:
    """Create the configured data store and return its validated settings."""
    settings = load_data_config()
    store = MarketDataStore(settings.adjusted_dir, settings.duckdb_path)
    return store, settings


def _commit_import(
    frame: pd.DataFrame, source_label: str, membership: pd.DataFrame | None = None
) -> None:
    """Persist imported bars, membership, and the refreshed observed calendar."""
    invalid_rows = int(frame.attrs.get("invalid_rows_skipped", 0))
    invalid_files = int(frame.attrs.get("invalid_files_skipped", 0))
    invalid_breakdown = frame.attrs.get("invalid_row_breakdown", [])
    store, settings = _store()
    merged = store.write_ohlcv(attach_raw_close(frame))
    store.write_calendar(build_calendar(merged, settings.holidays))
    store.write_universe(
        membership
        if membership is not None
        else load_universe(config_directory() / "universe.yaml")
    )
    if invalid_breakdown:
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(invalid_breakdown).to_csv(
            settings.reports_dir / "import_skipped_rows.csv", index=False
        )
    logger.info("Imported %s rows from %s", len(frame), source_label)
    typer.echo(
        f"Imported {len(frame)} rows; store now contains {len(merged)} rows. "
        f"Skipped {invalid_rows} rows with incomplete required OHLCV in {invalid_files} files. "
        + (
            "See reports/import_skipped_rows.csv for file/year/column counts."
            if invalid_rows
            else ""
        )
    )


@data_app.command("import-eod2")
def import_eod2(
    path: Annotated[
        Path, typer.Option("--path", exists=True, readable=True, help="EOD2 CSV path.")
    ],
) -> None:
    """Import adjusted daily prices from a local EOD2 CSV."""
    frame = EOD2Source().load(path)
    _commit_import(frame, str(path))


@data_app.command("import-pit")
def import_pit(
    path: Annotated[
        Path,
        typer.Option("--path", exists=True, readable=True, help="Raw PIT CSV or Parquet file."),
    ],
    actions: Annotated[
        Path | None,
        typer.Option("--actions", exists=True, readable=True, help="Corporate-action CSV."),
    ] = None,
    membership: Annotated[
        Path | None,
        typer.Option("--membership", exists=True, readable=True, help="PIT membership CSV."),
    ] = None,
) -> None:
    """Import raw PIT bars, apply corporate actions, and store dated membership."""
    settings = load_data_config()
    action_path = actions or settings.corporate_actions_path
    if action_path is None:
        raise typer.BadParameter(
            "Provide --actions or configure corporate_actions_path in data.yaml"
        )
    raw = PitHistorySource().load(path)
    action_frame = load_corporate_actions(action_path)
    adjusted = apply_corporate_actions(raw, action_frame)
    findings = validate_adjustments(adjusted, action_frame, settings.maximum_single_day_return_pct)
    if not findings.empty:
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        findings.to_csv(settings.reports_dir / "corporate_action_review.csv", index=False)
    membership_path = membership or settings.pit_membership_path
    membership_frame = load_pit_membership(membership_path) if membership_path is not None else None
    _commit_import(adjusted, str(path), membership_frame)
    typer.echo(
        f"Imported {len(adjusted)} PIT rows from {path} with {len(action_frame)} action dates; "
        f"{len(findings)} adjusted returns need review."
    )


@data_app.command("verify-pit")
def verify_pit(
    path: Annotated[
        Path,
        typer.Option("--path", exists=True, readable=True, help="Raw PIT CSV or Parquet file."),
    ],
    actions: Annotated[
        Path | None,
        typer.Option("--actions", exists=True, readable=True, help="Corporate-action CSV."),
    ] = None,
    membership: Annotated[
        Path | None,
        typer.Option("--membership", exists=True, readable=True, help="PIT membership CSV."),
    ] = None,
) -> None:
    """Write evidence for a PIT data claim and fail if required coverage is missing."""
    settings = load_data_config()
    action_path = actions or settings.corporate_actions_path
    membership_path = membership or settings.pit_membership_path
    raw = PitHistorySource().load(path)
    checks: dict[str, bool] = {"bars_nonempty": not raw.empty}
    details: dict[str, object] = {
        "rows": len(raw),
        "symbols": int(raw["symbol"].nunique()),
    }

    if settings.require_corporate_actions and action_path is None:
        checks["corporate_actions_supplied"] = False
    elif action_path is not None:
        action_frame = load_corporate_actions(action_path)
        adjusted = apply_corporate_actions(raw, action_frame)
        findings = validate_adjustments(
            adjusted, action_frame, settings.maximum_single_day_return_pct
        )
        checks["corporate_actions_supplied"] = True
        checks["adjusted_returns_continuous"] = findings.empty
        details["action_dates"] = len(action_frame)
        details["large_adjusted_returns"] = len(findings)
    else:
        checks["corporate_actions_supplied"] = True
        checks["adjusted_returns_continuous"] = True

    if settings.require_pit_membership and membership_path is None:
        checks["membership_supplied"] = False
    elif membership_path is not None:
        membership_frame = load_pit_membership(membership_path)
        bar_symbols = set(raw["symbol"].astype(str))
        member_symbols = set(membership_frame["symbol"].astype(str))
        checks["membership_supplied"] = True
        checks["membership_covers_all_symbols"] = bar_symbols.issubset(member_symbols)
        details["symbols_without_membership"] = sorted(bar_symbols.difference(member_symbols))
    else:
        checks["membership_supplied"] = True
        checks["membership_covers_all_symbols"] = True

    passed = all(checks.values())
    report = {
        "disclaimer": "Research output, not advice; no guarantee of profit.",
        "declared_data_bias_status": load_data_bias_status(load_config()),
        "path": str(path),
        "checks": checks,
        "details": details,
        "passed": passed,
        "note": "Changing data_bias_status to UNBIASED remains a manual review decision.",
    }
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = settings.reports_dir / "pit_verification.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    typer.echo(
        f"PIT verification {'passed' if passed else 'failed'}; checks: "
        + ", ".join(f"{name}={value}" for name, value in checks.items())
        + f"; report: {report_path}"
    )
    if not passed:
        raise typer.Exit(code=1)


@data_app.command("update")
def update_data(
    path: Annotated[
        Path | None, typer.Option("--path", exists=True, readable=True, help="Local bhavcopy file.")
    ] = None,
    url: Annotated[
        str | None, typer.Option("--url", help="Configured bhavcopy CSV or ZIP URL.")
    ] = None,
) -> None:
    """Import a downloaded NSE bhavcopy or fetch it from a configured URL."""
    settings = load_data_config()
    source_path = path
    if source_path is None:
        selected_url = url or settings.nse_bhavcopy_url
        if selected_url is None:
            raise typer.BadParameter("Provide --path, --url, or configure nse_bhavcopy_url")
        suffix = (
            ".zip" if selected_url.lower().split("?", maxsplit=1)[0].endswith(".zip") else ".csv"
        )
        source_path = download_bhavcopy(
            selected_url,
            settings.raw_dir / f"bhavcopy_latest{suffix}",
            settings.retry_attempts,
            settings.retry_wait_seconds,
            settings.request_timeout_seconds,
        )
    frame = BhavcopySource().load(source_path)
    _commit_import(frame, str(source_path))


@data_app.command("check")
def data_check() -> None:
    """Run data-quality checks and write reports/data_health.html."""
    store, settings = _store()
    report_path = settings.reports_dir / "data_health.html"
    config = load_config()
    data_bias_status = load_data_bias_status(config)
    universe_settings = liquidity_universe_settings()
    findings: list[pd.DataFrame] = []
    coverage: list[pd.DataFrame] = []
    critical_failures = 0
    total_rows = 0
    partitions = sorted(store.adjusted_dir.glob("year=*/ohlcv.parquet"))
    if not partitions:
        failure = pd.DataFrame(
            [
                {
                    "check": "empty_dataset",
                    "severity": "critical",
                    "symbol": "",
                    "date": None,
                    "detail": "No OHLCV partitions found",
                }
            ],
            columns=["check", "severity", "symbol", "date", "detail"],
        )
        write_health_report(
            QualityReport(failure, pd.DataFrame(), 1),
            report_path,
            data_bias_status == "SURVIVOR_BIASED",
            data_bias_status,
        )
        typer.echo(f"No OHLCV partitions; wrote failure report: {report_path}")
        raise typer.Exit(code=1)

    connection = duckdb.connect()
    try:
        by_year = {
            int(partition.parent.name.split("=", maxsplit=1)[1]): partition
            for partition in partitions
        }
        for partition_number, partition in enumerate(partitions, start=1):
            year = int(partition.parent.name.split("=", maxsplit=1)[1])
            typer.echo(f"Checking year {year} ({partition_number}/{len(partitions)})...")
            current = connection.execute("SELECT * FROM read_parquet(?)", [str(partition)]).df()
            total_rows += len(current)
            if current.empty:
                continue
            previous = by_year.get(year - 1)
            previous_frame = (
                connection.execute("SELECT * FROM read_parquet(?)", [str(previous)]).df()
                if previous is not None
                else None
            )
            frame = (
                pd.concat([previous_frame, current], ignore_index=True)
                if previous_frame is not None
                else current
            )
            calendar = build_calendar(frame, settings.holidays)
            membership = point_in_time_liquidity_membership(
                frame,
                int(universe_settings["top_n"]),
                int(universe_settings["trailing_days"]),
                float(universe_settings["minimum_price"]),
                int(universe_settings["minimum_history"]),
                universe_settings["excluded_symbol_pattern"],
            )
            liquid_symbols = set(frame.loc[membership, "symbol"].astype(str).unique())
            report = check_ohlcv(
                frame,
                calendar,
                settings.maximum_single_day_return_pct,
                settings.stale_close_days,
                settings.maximum_adjusted_raw_difference_pct,
                settings.minimum_analysis_date,
                liquid_symbols,
                membership,
                universe_settings["excluded_symbol_pattern"],
            )
            year_findings = report.findings.loc[
                report.findings["date"].map(
                    lambda day, expected_year=year: getattr(day, "year", expected_year)
                    == expected_year
                )
            ]
            findings.append(year_findings)
            coverage.append(report.coverage.loc[report.coverage["year"].eq(year)])
            critical_failures += int(year_findings["severity"].eq("critical").sum())
    except Exception as error:
        findings.append(
            pd.DataFrame(
                [
                    {
                        "check": "data_check_error",
                        "severity": "critical",
                        "symbol": "",
                        "date": None,
                        "detail": f"{type(error).__name__}: {error}",
                    }
                ],
                columns=["check", "severity", "symbol", "date", "detail"],
            )
        )
        critical_failures += 1
    finally:
        connection.close()
        all_findings = (
            pd.concat(findings, ignore_index=True)
            if findings
            else pd.DataFrame(columns=["check", "severity", "symbol", "date", "detail"])
        )
        all_coverage = (
            pd.concat(coverage, ignore_index=True).drop_duplicates("year", keep="last")
            if coverage
            else pd.DataFrame(columns=["year", "symbols", "observations"])
        )
        all_coverage = all_coverage.sort_values("year", ignore_index=True)
        final_report = QualityReport(all_findings, all_coverage, critical_failures)
        write_health_report(
            final_report,
            report_path,
            data_bias_status == "SURVIVOR_BIASED",
            data_bias_status,
        )
    typer.echo(
        f"Rows: {total_rows}; findings: {len(all_findings)}; "
        f"critical failures: {critical_failures}; report: {report_path}"
    )
    if critical_failures:
        raise typer.Exit(code=1)


@data_app.command("summary")
def data_summary() -> None:
    """Print coverage and date range from the local data store."""
    store, _settings = _store()
    frame = store.read_ohlcv()
    study_document = load_config().documents.get("study", {})
    benchmark = study_document.get("benchmark_symbol")
    if not isinstance(benchmark, str):
        benchmark = None
    if frame.empty:
        typer.echo(
            json.dumps(
                {
                    "rows": 0,
                    "symbols": 0,
                    "benchmark_symbol": benchmark,
                    "benchmark_present": False,
                    "data_bias_status": load_data_bias_status(load_config()),
                },
                indent=2,
            )
        )
        return
    years = pd.to_datetime(frame["date"]).dt.year
    typer.echo(
        json.dumps(
            {
                "rows": len(frame),
                "symbols": int(frame["symbol"].nunique()),
                "benchmark_symbol": benchmark,
                "data_bias_status": load_data_bias_status(load_config()),
                "benchmark_present": (
                    bool(frame["symbol"].eq(benchmark).any()) if benchmark is not None else False
                ),
                "start_date": str(frame["date"].min()),
                "end_date": str(frame["date"].max()),
                "coverage_by_year": frame.assign(year=years)
                .groupby("year")["symbol"]
                .nunique()
                .astype(int)
                .to_dict(),
            },
            indent=2,
        )
    )


def _approved_backtest_setups() -> list[SetupConfig]:
    """Load only setup configurations that Phase 2 marked as approved."""
    path = config_directory() / "approved_setups.yaml"
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing {path}; run Phase 2 study report and review its approval gates first"
        )
    with path.open(encoding="utf-8") as config_file:
        document = yaml.safe_load(config_file)
    entries = document.get("approved_setups", []) if isinstance(document, dict) else []
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"No approved setups are listed in {path}")
    configs = [SetupConfig.model_validate(item["parameters"]) for item in entries]
    unique = {
        json.dumps(config.model_dump(mode="json"), sort_keys=True): config for config in configs
    }
    return list(unique.values())


def _walk_forward_candidates() -> list[SetupConfig]:
    """Expand declared parameter grids without using validation or holdout results."""
    candidates: list[SetupConfig] = []
    for path in sorted((config_directory() / "setups").glob("*.yaml")):
        config = load_setup_config(path.stem, path.parent)
        names = sorted(config.sweep_grid)
        if not names:
            candidates.append(config)
            continue
        value_lists = [config.sweep_grid[name] for name in names]
        for combination in itertools.product(*value_lists):
            params = {**config.params, **dict(zip(names, combination, strict=True))}
            candidates.append(config.model_copy(update={"params": params}))
    return candidates


def _save_portfolio_outputs(
    label: str,
    simulation: dict[str, pd.DataFrame],
    settings: Any,
    metrics: dict[str, Any],
    stress: dict[str, dict[str, Any]],
    monte_carlo: dict[str, float | int],
    setups: list[SetupConfig],
    split_label: str,
) -> Path:
    """Persist one immutable result bundle and append its experiments to the registry."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    output_dir = settings.reports_dir / f"backtest_{label}_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=False)
    simulation["equity"].to_parquet(output_dir / "equity.parquet", index=False)
    simulation["trades"].to_parquet(output_dir / "trades.parquet", index=False)
    simulation.get("orders", pd.DataFrame()).to_parquet(output_dir / "orders.parquet", index=False)
    if "windows" in simulation:
        simulation["windows"].to_parquet(output_dir / "windows.parquet", index=False)
    config = load_config()
    data_bias_status = load_data_bias_status(config)
    bias = data_bias_status == "SURVIVOR_BIASED"
    report_path = write_backtest_report(
        simulation["equity"],
        simulation["trades"],
        metrics,
        stress,
        monte_carlo,
        output_dir / "report.html",
        bias,
        data_bias_status,
    )
    study_settings = load_study_settings()
    registry = TrialRegistry(study_settings.trial_registry_path)
    logged_metrics = {
        "experiment_type": "portfolio_backtest",
        "portfolio_metrics": metrics,
        "cost_stress": stress,
        "monte_carlo": monte_carlo,
        "report_path": str(report_path),
    }
    for setup in setups:
        registry.append_trial(
            setup,
            f"backtest:{split_label}",
            settings.maximum_holding_days,
            logged_metrics,
        )
    typer.echo(f"Wrote backtest report: {report_path}")
    typer.echo(json.dumps(metrics, indent=2, default=str))
    return report_path


@backtest_app.command("run")
def backtest_run(
    split: Annotated[
        Literal["train", "validation"],
        typer.Option("--split", help="Run only inside the train or validation date range."),
    ] = "train",
) -> None:
    """Run an approved-setup portfolio backtest on a locked non-holdout split."""
    try:
        bounds = configured_split(split)
        if not isinstance(bounds.end, date):
            raise ValueError(f"{split} split must have a fixed end date")
        setups_config = _approved_backtest_setups()
        setups = [create_setup(setup_config) for setup_config in setups_config]
        simulation_settings = load_backtest_settings()
        costs = load_costs_config()
        store, _ = _store()
        frame = store.read_ohlcv_through(bounds.end)
        simulation = run_portfolio_backtest(
            frame, setups, simulation_settings, costs, bounds.start, bounds.end
        )
        metrics = portfolio_metrics(
            simulation["equity"], simulation["trades"], simulation_settings.annual_risk_free_rate
        )
        stress = cost_stress_runs(
            frame, setups, simulation_settings, costs, bounds.start, bounds.end
        )
        monte_carlo = monte_carlo_drawdowns(
            simulation["trades"],
            simulation_settings.monte_carlo_replicates,
            load_config().base.random_seed,
        )
        _save_portfolio_outputs(
            "run",
            simulation,
            simulation_settings,
            metrics,
            stress,
            monte_carlo,
            setups_config,
            split,
        )
    except (FileNotFoundError, ValueError) as error:
        typer.echo(f"Backtest failed: {error}", err=True)
        raise typer.Exit(code=1) from error


@backtest_app.command("walk-forward")
def backtest_walk_forward(
    split: Annotated[
        Literal["train", "validation"],
        typer.Option("--split", help="Out-of-sample train or validation range."),
    ] = "train",
) -> None:
    """Select setup parameters on rolling training windows and test the next window."""
    try:
        bounds = configured_split(split)
        if not isinstance(bounds.end, date):
            raise ValueError(f"{split} split must have a fixed end date")
        candidate_configs = _walk_forward_candidates()
        candidates = [create_setup(config) for config in candidate_configs]
        simulation_settings = load_backtest_settings()
        costs = load_costs_config()
        store, _ = _store()
        train_bounds = configured_split("train")
        available_start = train_bounds.start
        frame = store.read_ohlcv_through(bounds.end)
        if split == "train":
            oos_start = None
        else:
            oos_start = bounds.start
        result = walk_forward_backtest(
            frame,
            candidates,
            simulation_settings,
            costs,
            available_start,
            bounds.end,
            simulation_settings.walk_forward_train_years,
            simulation_settings.walk_forward_test_years,
            simulation_settings.walk_forward_step_years,
            oos_start_date=oos_start,
        )
        if result["equity"].empty:
            raise ValueError(
                "No complete walk-forward test window fits this split; "
                "check the configured date range"
            )
        metrics = portfolio_metrics(
            result["equity"], result["trades"], simulation_settings.annual_risk_free_rate
        )
        stress: dict[str, dict[str, Any]] = {}
        for multiplier in costs.stress_multipliers:
            stressed = walk_forward_backtest(
                frame,
                candidates,
                simulation_settings,
                costs,
                available_start,
                bounds.end,
                simulation_settings.walk_forward_train_years,
                simulation_settings.walk_forward_test_years,
                simulation_settings.walk_forward_step_years,
                oos_start_date=oos_start,
                cost_multiplier=multiplier,
            )
            stress[f"{multiplier:g}x"] = portfolio_metrics(
                stressed["equity"], stressed["trades"], simulation_settings.annual_risk_free_rate
            )
        monte_carlo = monte_carlo_drawdowns(
            result["trades"],
            simulation_settings.monte_carlo_replicates,
            load_config().base.random_seed,
        )
        selected_by_key = {
            json.dumps(item, sort_keys=True): SetupConfig.model_validate(item)
            for item in result["windows"]["selected_parameters"]
        }
        selected_configs = list(selected_by_key.values())
        _save_portfolio_outputs(
            "walk_forward",
            result,
            simulation_settings,
            metrics,
            stress,
            monte_carlo,
            selected_configs or candidate_configs,
            f"walk_forward:{split}",
        )
    except (FileNotFoundError, ValueError) as error:
        typer.echo(f"Walk-forward failed: {error}", err=True)
        raise typer.Exit(code=1) from error


def _study_inputs(
    split: SplitName, unlock_holdout: bool, registry: TrialRegistry
) -> tuple[pd.DataFrame, date, date]:
    """Read only the requested split, preserving the explicit holdout access lock."""
    bounds = configured_split(split)
    if split == "holdout":
        if not unlock_holdout:
            raise PermissionError(
                "holdout is locked; pass --unlock-holdout to record and access it"
            )
        registry.record_holdout_unlock()
        store, _data_settings = _store()
        frame = store.read_ohlcv()
        start, end = resolve_split(frame, split, True, registry, access_already_logged=True)
        return frame, start, end
    if not isinstance(bounds.end, date):
        raise ValueError("Locked train and validation end dates must be explicit in splits.yaml")
    store, _data_settings = _store()
    frame = store.read_ohlcv_through(bounds.end)
    start, end = resolve_split(frame, split, False, registry)
    return frame, start, end


def _summarize_horizon(
    events: pd.DataFrame,
    horizon: int,
    costs_multipliers: list[float],
    settings: Any,
    seed: int,
) -> dict[str, Any]:
    """Create event, excess, yearly, breadth, cost-stress, and bootstrap metrics."""
    subset = events.loc[events["horizon"] == horizon].copy() if not events.empty else pd.DataFrame()
    empty_summary: dict[str, Any] = {"n_events": 0, "n_independent_dates": 0}
    net_metrics = return_summary(subset) if not subset.empty else empty_summary.copy()
    excess_metrics = (
        return_summary(subset, "excess_over_baseline") if not subset.empty else empty_summary.copy()
    )
    regime_excess_metrics = (
        return_summary(subset, "excess_over_regime_baseline")
        if not subset.empty
        else empty_summary.copy()
    )
    stress: dict[str, dict[str, Any]] = {}
    for multiplier in costs_multipliers:
        key = f"net_return_{multiplier:g}x_cost"
        column = f"net_return_{multiplier:g}x_cost"
        stress[key] = return_summary(subset, column) if not subset.empty else empty_summary.copy()
    yearly = yearly_returns(subset).to_dict(orient="records") if not subset.empty else []
    ci = (
        block_bootstrap_expectancy(
            subset,
            "net_return",
            settings.bootstrap_replicates,
            settings.bootstrap_block_length,
            seed,
            0.95,
        )
        if not subset.empty
        else (float("nan"), float("nan"))
    )
    return {
        "n_events": net_metrics.get("n_events", 0),
        "n_independent_dates": net_metrics.get("n_independent_dates", 0),
        "net_return": net_metrics,
        "excess_over_baseline": excess_metrics,
        "excess_over_regime_baseline": regime_excess_metrics,
        "yearly": yearly,
        "breadth": symbol_breadth(subset) if not subset.empty else 0.0,
        "bootstrap_expectancy_ci_95": {"lower": ci[0], "upper": ci[1]},
        **stress,
    }


def _study_warmup_bars(setup_config: SetupConfig) -> int:
    """Return the longest declared indicator window, ignoring non-window parameters."""
    window_names = ("lookback", "window", "length", "period", "days")
    values = [
        int(value)
        for name, value in setup_config.params.items()
        if any(part in name for part in window_names)
        and isinstance(value, int | float)
        and not isinstance(value, bool)
    ]
    return max(values, default=1)


def _execute_study(
    setup_config: SetupConfig,
    split: SplitName,
    unlock_holdout: bool,
    registry: TrialRegistry,
    settings: Any,
    costs_config: Any,
    seed: int,
    universe_path: Path | None = None,
    provisional: bool = False,
    setup: Setup | None = None,
    control: bool = False,
) -> list[dict[str, Any]]:
    """Run one setup/split experiment and append one registry row per horizon."""
    frame, start, end = _study_inputs(split, unlock_holdout, registry)
    active_setup = setup if setup is not None else create_setup(setup_config)
    membership: pd.Series | None = None
    if setup_config.universe == "nse_equities":
        universe_settings = liquidity_universe_settings(universe_path)
        membership = point_in_time_liquidity_membership(
            frame,
            int(universe_settings["top_n"]),
            int(universe_settings["trailing_days"]),
            float(universe_settings["minimum_price"]),
            int(universe_settings["minimum_history"]),
            universe_settings["excluded_symbol_pattern"],
        )
    history_rows = _study_warmup_bars(setup_config)
    history = frame.loc[frame["date"] < start].groupby("symbol", sort=False).tail(history_rows)
    in_split = frame.loc[frame["date"] >= start]
    frame = pd.concat([history, in_split], ignore_index=False).reset_index(drop=True)
    if membership is not None:
        membership = pd.Series(
            membership.reindex(pd.concat([history, in_split], ignore_index=False).index).to_numpy(
                dtype=bool
            ),
            index=frame.index,
        )
    try:
        events = build_event_study(
            frame,
            active_setup,
            settings,
            costs_config,
            start,
            end,
            point_in_time_membership=membership,
        )
    except Exception as error:
        for horizon in settings.horizons:
            registry.append_trial(
                setup_config,
                split,
                horizon,
                {
                    "status": "error",
                    "error": str(error),
                    **({"provisional": True} if provisional else {}),
                    **({"control": True} if control else {}),
                },
            )
        raise
    settings.events_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for horizon in settings.horizons:
        metrics = _summarize_horizon(
            events, horizon, costs_config.stress_multipliers, settings, seed
        )
        event_path: Path | None = None
        if not events.empty:
            event_path = settings.events_dir / (
                f"{setup_config.name}_{split}_{horizon}_{uuid4().hex}.parquet"
            )
            horizon_events = events.loc[events["horizon"] == horizon].copy()
            horizon_events.to_parquet(event_path, index=False)
        metrics["event_data_path"] = str(event_path) if event_path else None
        if provisional:
            metrics["provisional"] = True
        if control:
            metrics["control"] = True
        trial_id = registry.append_trial(setup_config, split, horizon, metrics)
        results.append({"trial_id": trial_id, "split": split, "horizon": horizon, **metrics})
    return results


@study_app.command("run")
def study_run(
    setup_name: Annotated[str, typer.Option("--setup", help="Registered setup name.")],
    split: Annotated[SplitName, typer.Option("--split", help="Train, validation, or holdout.")],
    unlock_holdout: Annotated[
        bool, typer.Option("--unlock-holdout", help="Audit and unlock holdout access.")
    ] = False,
    provisional: Annotated[
        bool, typer.Option("--provisional", help="Kill-stage mode: train/validation, no approvals.")
    ] = False,
) -> None:
    """Run a setup event study on one configured split."""
    _guard_provisional_split(split, provisional)
    try:
        project_config = load_config()
        settings = load_study_settings()
        costs = load_costs_config()
        setup_config = load_setup_config(setup_name, config_directory() / "setups")
        registry = TrialRegistry(settings.trial_registry_path)
        results = _execute_study(
            setup_config,
            split,
            unlock_holdout,
            registry,
            settings,
            costs,
            project_config.base.random_seed,
            universe_path=settings.provisional_universe_path if provisional else None,
            provisional=provisional,
        )
    except PermissionError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    except (ValueError, FileNotFoundError) as error:
        typer.echo(f"Study failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    if provisional:
        typer.echo(PROVISIONAL_BANNER, err=True)
    typer.echo(
        json.dumps(
            {
                "setup": setup_name,
                "split": split,
                "mode": "provisional" if provisional else "standard",
                "data_bias_status": load_data_bias_status(project_config),
                "results": results,
                "disclaimer": "Research output, not advice; no guarantee of profit.",
            },
            indent=2,
            allow_nan=True,
        )
    )


@study_app.command("sweep")
def study_sweep(
    setup_name: Annotated[str, typer.Option("--setup", help="Registered setup name.")],
    split: Annotated[SplitName, typer.Option("--split", help="Train, validation, or holdout.")],
    unlock_holdout: Annotated[
        bool, typer.Option("--unlock-holdout", help="Audit and unlock holdout access.")
    ] = False,
    provisional: Annotated[
        bool, typer.Option("--provisional", help="Kill-stage mode: train/validation, no approvals.")
    ] = False,
) -> None:
    """Run every declared parameter value and append each experiment."""
    _guard_provisional_split(split, provisional)
    try:
        project_config = load_config()
        settings = load_study_settings()
        costs = load_costs_config()
        base_setup = load_setup_config(setup_name, config_directory() / "setups")
        registry = TrialRegistry(settings.trial_registry_path)
        trials = []
        for parameter, values in base_setup.sweep_grid.items():
            for value in values:
                updated_params = {**base_setup.params, parameter: value}
                trial_setup = base_setup.model_copy(update={"params": updated_params})
                trials.extend(
                    _execute_study(
                        trial_setup,
                        split,
                        unlock_holdout,
                        registry,
                        settings,
                        costs,
                        project_config.base.random_seed,
                        universe_path=settings.provisional_universe_path if provisional else None,
                        provisional=provisional,
                    )
                )
    except PermissionError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    except (ValueError, FileNotFoundError) as error:
        typer.echo(f"Study sweep failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    if provisional:
        typer.echo(PROVISIONAL_BANNER, err=True)
    typer.echo(
        json.dumps(
            {
                "setup": setup_name,
                "split": split,
                "mode": "provisional" if provisional else "standard",
                "data_bias_status": load_data_bias_status(project_config),
                "trials": trials,
                "disclaimer": "Research output, not advice; no guarantee of profit.",
            },
            indent=2,
        )
    )


@study_app.command("positive-control")
def study_positive_control() -> None:
    """Run a deliberately invalid look-ahead control on real data as a wiring check."""
    project_config = load_config()
    settings = load_study_settings()
    costs = load_costs_config()
    gates = load_gate_settings()
    control_config = load_setup_config("__control_lookahead__", config_directory() / "controls")
    registry = TrialRegistry(settings.control_registry_path)
    results: list[dict[str, Any]] = []
    for split in ("train", "validation"):
        results.extend(
            _execute_study(
                control_config,
                split,
                False,
                registry,
                settings,
                costs,
                project_config.base.random_seed,
                universe_path=settings.provisional_universe_path,
                provisional=True,
                setup=LookAheadControl(control_config),
                control=True,
            )
        )
    summary = summarize_control_results(registry.rows(), gates)
    typer.echo(
        json.dumps(
            {
                "control": LookAheadControl.name,
                "label": "POSITIVE CONTROL: INVALID LOOK-AHEAD, NOT RESEARCH EVIDENCE",
                "excluded_from": ["multiple_testing_totals", "study_reports", "approvals"],
                "data_bias_status": load_data_bias_status(project_config),
                "verdict": "control_pass" if summary["pass"] else "control_fail",
                "splits_run": ["train", "validation"],
                "trials_appended": len(results),
                **summary,
                "disclaimer": "Research output, not advice; no guarantee of profit.",
            },
            indent=2,
            allow_nan=True,
        )
    )


def _eligible_report_rows(registry: TrialRegistry, provisional: bool) -> list[dict[str, Any]]:
    """Return report rows, excluding diagnostic controls and non-requested trials."""
    rows = [
        row
        for row in registry.rows()
        if not row["split"].startswith("backtest:")
        and json.loads(row["metrics_json"]).get("control") is not True
    ]
    if provisional:
        rows = [
            row
            for row in rows
            if row["split"] in {"train", "validation"}
            and json.loads(row["metrics_json"]).get("provisional") is True
        ]
    return rows


@study_app.command("report")
def study_report(
    provisional: Annotated[
        bool, typer.Option("--provisional", help="Report only kill-stage trials; never approve.")
    ] = False,
) -> None:
    """Write an HTML trial/FDR/gate report; provisional mode never writes approvals."""
    settings = load_study_settings()
    gates = load_gate_settings()
    config = load_config()
    data_bias_status = load_data_bias_status(config)
    registry = TrialRegistry(settings.trial_registry_path)
    rows = _eligible_report_rows(registry, provisional)
    p_values: list[float] = []
    for row in rows:
        metrics = json.loads(row["metrics_json"])
        value = metrics.get("excess_over_baseline", {}).get("date_clustered_p")
        p_values.append(float(value) if isinstance(value, int | float) else float("nan"))
    adjusted = benjamini_hochberg(p_values)
    report_rows: list[dict[str, Any]] = []
    approved: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
    for row, adjusted_p in zip(rows, adjusted, strict=True):
        key = (row["setup_name"], row["params_hash"], row["horizon"])
        grouped.setdefault(key, []).append({**row, "adjusted_p_value": adjusted_p})
    for (setup_name, params_hash, horizon), trials in grouped.items():
        metrics_by_split = {trial["split"]: json.loads(trial["metrics_json"]) for trial in trials}
        matched = [trial for trial in trials if trial["split"] == "validation"]
        adj_p = float(matched[0]["adjusted_p_value"]) if matched else float("nan")
        rationale = ""
        yearly_parts: list[pd.DataFrame] = []
        breadth_values: list[float] = []
        for trial in trials:
            metrics = json.loads(trial["metrics_json"])
            params = json.loads(trial["params_json"])
            rationale = str(params.get("rationale", rationale))
            if metrics.get("yearly"):
                yearly_parts.append(pd.DataFrame(metrics["yearly"]))
            if isinstance(metrics.get("breadth"), int | float):
                breadth_values.append(float(metrics["breadth"]))
        yearly_frame = (
            pd.concat(yearly_parts, ignore_index=True)
            .groupby("year", as_index=False)
            .agg(
                events=("events", "sum"),
                expectancy=("expectancy", "mean"),
                total_profit=("total_profit", "sum"),
            )
            if yearly_parts
            else pd.DataFrame()
        )
        neighbour_values = _neighbour_expectancies(
            setup_name, params_hash, horizon, trials, rows, gates
        )
        gate_args = (
            metrics_by_split,
            adj_p,
            yearly_frame,
            min(breadth_values) if breadth_values else 0.0,
            neighbour_values,
            rationale,
            gates,
        )
        if provisional:
            gate_result = evaluate_provisional_gates(*gate_args)
            verdict = "provisional_pass" if gate_result.provisional_pass else "rejected"
        else:
            gate_result = evaluate_setup_gates(*gate_args, data_bias_status)
            verdict = (
                "approved"
                if gate_result.approved
                else "provisional_pass"
                if gate_result.provisional_pass
                else "rejected"
            )
        report_rows.append(
            {
                "setup": setup_name,
                "params_hash": params_hash,
                "horizon": horizon,
                "verdict": verdict,
                "adjusted_p_value": adj_p,
                "checks": gate_result.checks,
            }
        )
        if gate_result.approved:
            representative = json.loads(trials[0]["params_json"])
            approved.append(
                {
                    "setup": setup_name,
                    "params_hash": params_hash,
                    "horizon": horizon,
                    "parameters": representative,
                    "validated_metrics": metrics_by_split,
                }
            )
    bias_status = data_bias_status.replace("_", " ").lower()
    report_path = (
        settings.provisional_report_path if provisional else Path("reports/study_report.html")
    )
    banner_html = (
        "<div style='border:3px solid #b00;background:#fff3f3;padding:1rem;"
        "font-size:1.4rem;font-weight:bold;color:#b00'>"
        + html.escape(PROVISIONAL_BANNER)
        + "<br><span style='font-size:1rem;font-weight:normal'>"
        "Train/validation only. A fail discards the setup; a pass is a candidate, "
        "not a result. No approved_setups.yaml is written.</span></div>"
        if provisional
        else ""
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    rows_html_parts = []
    for row in report_rows:
        failed_gates = ", ".join(
            name for name, check in row["checks"].items() if not check["passed"]
        )
        rows_html_parts.append(
            "<tr>"
            f"<td>{html.escape(str(row['setup']))}</td><td>{row['horizon']}</td>"
            f"<td>{html.escape(str(row['verdict']))}</td><td>{row['adjusted_p_value']:.6g}</td>"
            f"<td>{html.escape(failed_gates)}</td>"
            "</tr>"
        )
    rows_html = "".join(rows_html_parts)
    html_report = (
        "<!doctype html><html><head><meta charset='utf-8'><title>Study report</title></head><body>"
        "<h1>Event-study trial report</h1>"
        f"{banner_html}"
        "<p>Research output, not advice; no guarantee of profit.</p>"
        f"<p>Survivorship-bias status: {html.escape(bias_status)}</p>"
        f"<p>Data-bias status: {html.escape(data_bias_status)}</p>"
        f"<p>Trials in this report: {len(rows)} (registry total {registry.trial_count()})</p>"
        "<table><thead><tr><th>Setup</th><th>Horizon</th><th>Verdict</th>"
        "<th>BH adjusted p</th><th>Failed gates</th></tr></thead>"
        f"<tbody>{rows_html}</tbody></table></body></html>"
    )
    report_path.write_text(html_report, encoding="utf-8")
    if provisional:
        typer.echo(
            f"Provisional mode did not write {config_directory() / 'approved_setups.yaml'}",
            err=True,
        )
    else:
        approval_path = config_directory() / "approved_setups.yaml"
        if data_bias_status == "SURVIVOR_BIASED":
            approved = []
        approval_path.write_text(
            yaml.safe_dump({"approved_setups": approved}, sort_keys=False), encoding="utf-8"
        )
    if provisional:
        typer.echo(PROVISIONAL_BANNER, err=True)
    typer.echo(
        f"Wrote {report_path}; mode {'provisional' if provisional else 'standard'}; "
        f"data bias {data_bias_status}; approved {0 if provisional else len(approved)}; "
        f"provisional passes {sum(row['verdict'] == 'provisional_pass' for row in report_rows)}."
    )


def _neighbour_expectancies(
    setup_name: str,
    params_hash: str,
    horizon: int,
    selected_trials: list[dict[str, Any]],
    all_rows: list[dict[str, Any]],
    settings: Any,
) -> list[float]:
    """Find configured adjacent sweep values and return their worst split expectancy."""
    if not selected_trials:
        return []
    params_json = json.loads(selected_trials[0]["params_json"])
    selected_params = params_json.get("params", {})
    setup_config = load_setup_config(setup_name, config_directory() / "setups")
    neighbours: list[float] = []
    for parameter, declared_values in setup_config.sweep_grid.items():
        ordered = sorted(set(declared_values))
        current = selected_params.get(parameter)
        if current not in ordered:
            continue
        position = ordered.index(current)
        adjacent = ordered[max(position - 1, 0) : position] + ordered[position + 1 : position + 2]
        for value in adjacent:
            neighbour_params = {**selected_params, parameter: value}
            serialized = json.dumps(
                {**params_json, "params": neighbour_params}, sort_keys=True, separators=(",", ":")
            )
            neighbour_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            matches = [
                row
                for row in all_rows
                if row["setup_name"] == setup_name
                and row["params_hash"] == neighbour_hash
                and row["horizon"] == horizon
            ]
            if not matches:
                continue
            stressed = []
            for row in matches:
                metrics = json.loads(row["metrics_json"])
                stress_key = f"net_return_{settings.minimum_cost_stress_multiplier:g}x_cost"
                stress = metrics.get(stress_key, {}).get("expectancy")
                if isinstance(stress, int | float):
                    stressed.append(float(stress))
                else:
                    stressed.append(float("-inf"))
            if {row["split"] for row in matches} >= {"train", "validation", "holdout"}:
                neighbours.append(min(stressed))
    return neighbours


@paper_app.command("run")
def paper_run(
    run_date: Annotated[
        date | None,
        typer.Option("--date", help="Signal date YYYY-MM-DD; defaults to the latest session."),
    ] = None,
    data_path: Annotated[
        Path | None,
        typer.Option("--data-path", exists=True, readable=True, help="Optional local bhavcopy."),
    ] = None,
    data_url: Annotated[
        str | None, typer.Option("--data-url", help="Optional bhavcopy URL to import first.")
    ] = None,
) -> None:
    """Run one idempotent paper-trading day and write its report and order plan."""
    try:
        if data_path is not None or data_url is not None:
            update_data(path=data_path, url=data_url)
        paper = load_paper_settings()
        settings = load_backtest_settings()
        costs = load_costs_config()
        bias = load_data_bias_status(load_config())
        approved = load_approval_entries()
        store, _data_settings = _store()
        frame = store.read_ohlcv()
        if frame.empty:
            raise ValueError("No market data is available; import data first")
        available_dates = sorted(pd.to_datetime(frame["date"]).dt.date.unique())
        selected = run_date or available_dates[-1]
        if selected not in available_dates:
            raise ValueError(f"No market data is available on {selected.isoformat()}")
        setups = [
            create_setup(SetupConfig.model_validate(entry["parameters"])) for entry in approved
        ]
        membership: pd.Series | None = None
        if any(setup.config.universe == "nse_equities" for setup in setups):
            universe_settings = liquidity_universe_settings()
            membership = point_in_time_liquidity_membership(
                frame,
                int(universe_settings["top_n"]),
                int(universe_settings["trailing_days"]),
                float(universe_settings["minimum_price"]),
                int(universe_settings["minimum_history"]),
                universe_settings["excluded_symbol_pattern"],
            )
        ledger = PaperLedger(paper.ledger_path)
        summary = run_paper_day(
            selected, frame, approved, settings, costs, paper, ledger, bias, membership
        )
    except (FileNotFoundError, ValueError) as error:
        typer.echo(f"Paper run failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        json.dumps(
            {
                "run_date": summary.run_date.isoformat(),
                "approved_setups": summary.approved_setups,
                "signals": summary.signals_count,
                "orders": summary.orders_count,
                "fills": summary.fills_count,
                "report": str(paper.reports_dir / f"paper_{selected.isoformat()}.md"),
                "data_bias_status": bias,
                "disclaimer": PAPER_DISCLAIMER,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    app()
