"""Command-line entry point for the research engine."""

from __future__ import annotations

import hashlib
import html
import json
import logging
from datetime import date
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import pandas as pd  # type: ignore[import-untyped]
import typer
import yaml

from nse_engine.backtest.costs import load_costs_config
from nse_engine.calendar import build_calendar
from nse_engine.config import DataConfig, config_directory, load_config, load_data_config
from nse_engine.data.adjust import attach_raw_close
from nse_engine.data.quality import check_ohlcv, write_health_report
from nse_engine.data.sources.eod2_import import EOD2Source
from nse_engine.data.sources.nselib_source import BhavcopySource, download_bhavcopy
from nse_engine.data.store import MarketDataStore
from nse_engine.data.universe import load_universe
from nse_engine.logging_config import configure_logging
from nse_engine.setups.base import SetupConfig, load_setup_config
from nse_engine.setups.registry import create_setup
from nse_engine.study.config import load_gate_settings, load_study_settings
from nse_engine.study.events import build_event_study
from nse_engine.study.gates import evaluate_setup_gates
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
logger = logging.getLogger(__name__)
app.add_typer(data_app, name="data")
app.add_typer(study_app, name="study")


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


def _execute_study(
    setup_config: SetupConfig,
    split: SplitName,
    unlock_holdout: bool,
    registry: TrialRegistry,
    settings: Any,
    costs_config: Any,
    seed: int,
) -> list[dict[str, Any]]:
    """Run one setup/split experiment and append one registry row per horizon."""
    frame, start, end = _study_inputs(split, unlock_holdout, registry)
    setup = create_setup(setup_config)
    try:
        events = build_event_study(frame, setup, settings, costs_config, start, end)
    except Exception as error:
        for horizon in settings.horizons:
            registry.append_trial(
                setup_config,
                split,
                horizon,
                {"status": "error", "error": str(error)},
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
) -> None:
    """Run a setup event study on one configured split."""
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
        )
    except PermissionError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    except (ValueError, FileNotFoundError) as error:
        typer.echo(f"Study failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        json.dumps(
            {
                "setup": setup_name,
                "split": split,
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
) -> None:
    """Run every declared parameter value and append each experiment."""
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
                    )
                )
    except PermissionError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    except (ValueError, FileNotFoundError) as error:
        typer.echo(f"Study sweep failed: {error}", err=True)
        raise typer.Exit(code=1) from error
    typer.echo(
        json.dumps(
            {
                "setup": setup_name,
                "split": split,
                "trials": trials,
                "disclaimer": "Research output, not advice; no guarantee of profit.",
            },
            indent=2,
        )
    )


@study_app.command("report")
def study_report() -> None:
    """Write an HTML trial/FDR/gate report and approved-setups registry."""
    settings = load_study_settings()
    gates = load_gate_settings()
    registry = TrialRegistry(settings.trial_registry_path)
    rows = registry.rows()
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
        gate_result = evaluate_setup_gates(
            metrics_by_split,
            adj_p,
            yearly_frame,
            min(breadth_values) if breadth_values else 0.0,
            neighbour_values,
            rationale,
            gates,
        )
        report_rows.append(
            {
                "setup": setup_name,
                "params_hash": params_hash,
                "horizon": horizon,
                "verdict": "approved" if gate_result.approved else "rejected",
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
    config = load_config()
    universe = config.documents.get("universe", {})
    biased = bool(universe.get("survivorship_biased", True))
    bias_status = (
        "static universe; survivorship bias likely" if biased else "point-in-time universe"
    )
    report_path = Path("reports/study_report.html")
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
        "<p>Research output, not advice; no guarantee of profit.</p>"
        f"<p>Survivorship-bias status: {html.escape(bias_status)}</p>"
        f"<p>Logged trials: {registry.trial_count()}</p>"
        "<table><thead><tr><th>Setup</th><th>Horizon</th><th>Verdict</th>"
        "<th>BH adjusted p</th><th>Failed gates</th></tr></thead>"
        f"<tbody>{rows_html}</tbody></table></body></html>"
    )
    report_path.write_text(html_report, encoding="utf-8")
    approval_path = config_directory() / "approved_setups.yaml"
    approval_path.write_text(
        yaml.safe_dump({"approved_setups": approved}, sort_keys=False), encoding="utf-8"
    )
    typer.echo(f"Wrote {report_path}; approved {len(approved)} setup/horizon combinations.")


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


if __name__ == "__main__":
    app()
