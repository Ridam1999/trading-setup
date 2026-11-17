"""Integration coverage for the train/validation-only kill stage."""

import json
import sqlite3
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from nse_engine.cli import app

runner = CliRunner()


def _write_config(config_dir: Path, tmp_path: Path) -> None:
    """Write an isolated train/validation configuration with no approvals."""
    config_dir.mkdir(parents=True)
    setup_dir = config_dir / "setups"
    setup_dir.mkdir()
    (config_dir / "base.yaml").write_text(
        "app_name: nse-setup-research-engine\ntimezone: Asia/Kolkata\n"
        "log_level: INFO\nrandom_seed: 7\n",
        encoding="utf-8",
    )
    (config_dir / "data.yaml").write_text(
        f"raw_dir: {tmp_path / 'raw'}\nadjusted_dir: {tmp_path / 'adjusted'}\n"
        f"duckdb_path: {tmp_path / 'db' / 'prices.duckdb'}\n"
        f"reports_dir: {tmp_path / 'reports'}\nminimum_analysis_date: 2024-01-01\n"
        "maximum_single_day_return_pct: 100\nmaximum_adjusted_raw_difference_pct: 2\n"
        "stale_close_days: 5\nretry_attempts: 2\nretry_wait_seconds: 0.1\n"
        "request_timeout_seconds: 1\nholidays: []\nnse_bhavcopy_url: null\n",
        encoding="utf-8",
    )
    (config_dir / "universe.yaml").write_text(
        "name: test_universe\nsymbols: [AAA, BBB]\nsurvivorship_biased: true\n",
        encoding="utf-8",
    )
    (config_dir / "universe_provisional.yaml").write_text(
        "point_in_time_liquidity:\n"
        "  top_n: 1\n"
        "  trailing_days: 1\n"
        "  minimum_price: 0\n"
        "  minimum_history: 1\n"
        "  excluded_symbol_pattern: null\n",
        encoding="utf-8",
    )
    (config_dir / "study.yaml").write_text(
        "horizons: [1]\nentry_limit_buffer_bps: 0\nnotional_rupees: 1000\n"
        "bootstrap_replicates: 10\nbootstrap_block_length: 2\nfdr_alpha: 0.05\n"
        "benchmark_symbol: BENCHMARK_MISSING\nbenchmark_trend_length: 20\n"
        "volatility_lookback: 5\n"
        f"trial_registry_path: {tmp_path / 'trials.sqlite'}\n"
        f"events_dir: {tmp_path / 'events'}\n"
        f"provisional_universe_path: {config_dir / 'universe_provisional.yaml'}\n"
        f"provisional_report_path: {tmp_path / 'reports' / 'study_report_provisional.html'}\n",
        encoding="utf-8",
    )
    (config_dir / "gates.yaml").write_text(
        "minimum_independent_dates: 2\nminimum_positive_year_fraction: 0.6\n"
        "minimum_positive_symbol_fraction: 0.55\nmaximum_single_year_profit_fraction: 0.4\n"
        "minimum_cost_stress_multiplier: 2.0\nmaximum_adjusted_p_value: 0.05\n"
        "require_positive_parameter_neighbours: false\n",
        encoding="utf-8",
    )
    (config_dir / "splits.yaml").write_text(
        "train: {start: 2024-01-01, end: 2024-03-31}\n"
        "validation: {start: 2024-04-01, end: 2024-06-30}\n"
        "holdout: {start: 2024-07-01, end: latest}\n",
        encoding="utf-8",
    )
    (config_dir / "costs.yaml").write_text(
        "delivery_equity: {brokerage_pct: 0, brokerage_min_per_order: 0, "
        "brokerage_max_per_order: 0, stt_pct_buy: 0, stt_pct_sell: 0, "
        "exchange_txn_pct: 0, sebi_fee_pct: 0, ipft_fee_pct: 0, stamp_duty_pct_buy: 0, "
        "gst_pct: 0, dp_charge_per_sell_scrip: 0}\nslippage_bps: 0\n"
        "stress_multipliers: [1.0, 2.0]\n",
        encoding="utf-8",
    )
    (config_dir / "research.yaml").write_text(
        "data_bias_status: SURVIVOR_BIASED\n", encoding="utf-8"
    )
    (setup_dir / "random_signal.yaml").write_text(
        "name: random_signal\nuniverse: nse_equities\nrationale: negative control\n"
        "params: {seed: 7, signal_probability: 0.05, traded_value_window: 1}\n"
        "filters: {minimum_price: 0, minimum_average_traded_value: 0}\n"
        "exit_rules: {}\nsweep_grid: {}\n",
        encoding="utf-8",
    )


def _write_eod2_data(directory: Path) -> None:
    """Write a small two-symbol EOD2 directory spanning both non-holdout splits."""
    directory.mkdir(parents=True)
    dates = pd.bdate_range("2024-01-01", periods=120).date
    for symbol in ("AAA", "BBB"):
        lines = ["DATE,OPEN,HIGH,LOW,CLOSE,VOLUME"]
        for index, day in enumerate(dates):
            close = 100.0 + index * 0.1
            lines.append(f"{day},{close},{close + 1},{close - 1},{close},10000")
        (directory / f"{symbol}.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_provisional_studies_never_approve_or_write_approvals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kill-stage runs use train/validation only, flag trials, and preserve approval files."""
    config_dir = tmp_path / "config"
    _write_config(config_dir, tmp_path)
    monkeypatch.setenv("NSE_ENGINE_CONFIG_DIR", str(config_dir))
    _write_eod2_data(tmp_path / "eod2")
    assert (
        runner.invoke(app, ["data", "import-eod2", "--path", str(tmp_path / "eod2")]).exit_code == 0
    )

    approval_path = config_dir / "approved_setups.yaml"
    sentinel = "approved_setups:\n  - setup: must_survive\n"
    approval_path.write_text(sentinel, encoding="utf-8")

    blocked = runner.invoke(
        app, ["study", "run", "--setup", "random_signal", "--split", "holdout", "--provisional"]
    )
    assert blocked.exit_code == 2
    assert "train/validation only" in blocked.output
    assert not (tmp_path / "trials.sqlite").exists()

    trained = runner.invoke(
        app, ["study", "run", "--setup", "random_signal", "--split", "train", "--provisional"]
    )
    assert trained.exit_code == 0, trained.output
    assert "SURVIVOR_BIASED" in trained.output
    assert '"mode": "provisional"' in trained.output
    validated = runner.invoke(
        app, ["study", "run", "--setup", "random_signal", "--split", "validation", "--provisional"]
    )
    assert validated.exit_code == 0, validated.output

    with sqlite3.connect(tmp_path / "trials.sqlite") as connection:
        trials = connection.execute(
            "SELECT split, metrics_json FROM trials WHERE setup_name = 'random_signal'"
        ).fetchall()
    assert {split for split, _ in trials} == {"train", "validation"}
    assert all(json.loads(metrics)["provisional"] is True for _, metrics in trials)

    reported = runner.invoke(app, ["study", "report", "--provisional"])
    assert reported.exit_code == 0, reported.output
    assert "SURVIVOR_BIASED" in reported.output
    html = (tmp_path / "reports" / "study_report_provisional.html").read_text(encoding="utf-8")
    assert "SURVIVOR_BIASED: PROVISIONAL KILL STAGE" in html
    assert "not a result" in html
    assert "<td>approved</td>" not in html
    assert approval_path.read_text(encoding="utf-8") == sentinel
