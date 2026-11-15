"""Command-line entry point for the research engine."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated

import pandas as pd  # type: ignore[import-untyped]
import typer

from nse_engine.calendar import build_calendar
from nse_engine.config import DataConfig, config_directory, load_config, load_data_config
from nse_engine.data.adjust import attach_raw_close
from nse_engine.data.quality import check_ohlcv, write_health_report
from nse_engine.data.sources.eod2_import import EOD2Source
from nse_engine.data.sources.nselib_source import BhavcopySource, download_bhavcopy
from nse_engine.data.store import MarketDataStore
from nse_engine.data.universe import load_universe
from nse_engine.logging_config import configure_logging

app = typer.Typer(help="Local NSE setup research tools.")
data_app = typer.Typer(help="Import and inspect daily market data.")
logger = logging.getLogger(__name__)
app.add_typer(data_app, name="data")


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


def _store() -> tuple[MarketDataStore, DataConfig]:
    """Create the configured data store and return its validated settings."""
    settings = load_data_config()
    store = MarketDataStore(settings.adjusted_dir, settings.duckdb_path)
    return store, settings


def _commit_import(frame: pd.DataFrame, source_label: str) -> None:
    """Persist imported bars and refresh the observed calendar."""
    store, settings = _store()
    merged = store.write_ohlcv(attach_raw_close(frame))
    store.write_calendar(build_calendar(merged, settings.holidays))
    store.write_universe(load_universe(config_directory() / "universe.yaml"))
    logger.info("Imported %s rows from %s", len(frame), source_label)
    typer.echo(f"Imported {len(frame)} rows; store now contains {len(merged)} rows.")


@data_app.command("import-eod2")
def import_eod2(
    path: Annotated[
        Path, typer.Option("--path", exists=True, readable=True, help="EOD2 CSV path.")
    ],
) -> None:
    """Import adjusted daily prices from a local EOD2 CSV."""
    frame = EOD2Source().load(path)
    _commit_import(frame, str(path))


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
    frame = store.read_ohlcv()
    calendar = build_calendar(frame, settings.holidays) if not frame.empty else pd.DataFrame()
    report = check_ohlcv(
        frame,
        calendar,
        settings.maximum_single_day_return_pct,
        settings.stale_close_days,
        settings.maximum_adjusted_raw_difference_pct,
        settings.minimum_analysis_date,
    )
    universe = load_config().documents.get("universe", {})
    biased = bool(universe.get("survivorship_biased", True))
    report_path = settings.reports_dir / "data_health.html"
    write_health_report(report, report_path, biased)
    typer.echo(
        f"Rows: {len(frame)}; findings: {len(report.findings)}; "
        f"critical failures: {report.critical_failures}; report: {report_path}"
    )
    if report.critical_failures:
        raise typer.Exit(code=1)


@data_app.command("summary")
def data_summary() -> None:
    """Print coverage and date range from the local data store."""
    store, _settings = _store()
    frame = store.read_ohlcv()
    if frame.empty:
        typer.echo("No market data loaded.")
        return
    years = pd.to_datetime(frame["date"]).dt.year
    typer.echo(
        json.dumps(
            {
                "rows": len(frame),
                "symbols": int(frame["symbol"].nunique()),
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


if __name__ == "__main__":
    app()
