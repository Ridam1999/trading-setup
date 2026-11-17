"""Integration coverage for the Phase 1 data CLI using local fixtures."""

from pathlib import Path

from typer.testing import CliRunner

from nse_engine.cli import app

runner = CliRunner()


def test_import_check_and_summary_commands(tmp_path: Path, monkeypatch) -> None:
    """The data CLI imports fixture CSV data and emits the health report."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "base.yaml").write_text(
        "app_name: nse-setup-research-engine\ntimezone: Asia/Kolkata\n"
        "log_level: INFO\nrandom_seed: 1\n",
        encoding="utf-8",
    )
    (config_dir / "data.yaml").write_text(
        f"raw_dir: {tmp_path / 'raw'}\nadjusted_dir: {tmp_path / 'adjusted'}\n"
        f"duckdb_path: {tmp_path / 'db' / 'prices.duckdb'}\n"
        f"reports_dir: {tmp_path / 'reports'}\nminimum_analysis_date: 2005-01-01\n"
        "maximum_single_day_return_pct: 100\nmaximum_adjusted_raw_difference_pct: 2\n"
        "stale_close_days: 5\nretry_attempts: 2\nretry_wait_seconds: 0.1\n"
        "request_timeout_seconds: 1\nholidays: []\nnse_bhavcopy_url: null\n",
        encoding="utf-8",
    )
    (config_dir / "universe.yaml").write_text(
        "name: test_universe\nsymbols: [AAA, BBB]\nsurvivorship_biased: true\n",
        encoding="utf-8",
    )
    (config_dir / "study.yaml").write_text("benchmark_symbol: NIFTYBEES\n", encoding="utf-8")
    monkeypatch.setenv("NSE_ENGINE_CONFIG_DIR", str(config_dir))
    eod2_directory = tmp_path / "eod2_data" / "daily"
    eod2_directory.mkdir(parents=True)
    for symbol, rows in {
        "AAA": "2024-01-02,100,105,99,104,12000\n2024-01-03,104,106,102,105,15000\n",
        "BBB": "2024-01-02,50,52,49,51,8000\n2024-01-03,51,53,50,52,9000\n",
    }.items():
        (eod2_directory / f"{symbol}.csv").write_text(
            f"DATE,OPEN,HIGH,LOW,CLOSE,VOLUME\n{rows}", encoding="utf-8"
        )

    imported = runner.invoke(app, ["data", "import-eod2", "--path", str(eod2_directory)])
    assert imported.exit_code == 0, imported.output
    bhavcopy = Path("tests/fixtures/bhavcopy_legacy_sample.csv").resolve()
    updated = runner.invoke(app, ["data", "update", "--path", str(bhavcopy)])
    assert updated.exit_code == 0, updated.output
    checked = runner.invoke(app, ["data", "check"])
    assert checked.exit_code == 0, checked.output
    summary = runner.invoke(app, ["data", "summary"])

    assert summary.exit_code == 0, summary.output
    assert '"rows": 4' in summary.output
    assert '"benchmark_present": false' in summary.output
    report = tmp_path / "reports" / "data_health.html"
    assert report.is_file()
    assert "Static universe; survivorship bias likely" in report.read_text(encoding="utf-8")
    assert (tmp_path / "adjusted" / "calendar" / "year=2024" / "calendar.parquet").is_file()
    assert (
        tmp_path / "adjusted" / "universe_membership" / "year=unknown" / "membership.parquet"
    ).is_file()


def test_data_help_lists_commands() -> None:
    """The real Typer help renderer exposes all supported data commands."""
    result = runner.invoke(app, ["data", "--help"])

    assert result.exit_code == 0, result.output
    assert "import-eod2" in result.output
    assert "update" in result.output
    assert "check" in result.output
    assert "summary" in result.output


def test_study_cli_lists_commands() -> None:
    """The study help exposes run, sweep, and report commands."""
    result = runner.invoke(app, ["study", "--help"])

    assert result.exit_code == 0, result.output
    assert "run" in result.output
    assert "sweep" in result.output
    assert "report" in result.output


def test_backtest_cli_lists_commands() -> None:
    """The backtest help exposes fixed-run and walk-forward commands."""
    result = runner.invoke(app, ["backtest", "--help"])

    assert result.exit_code == 0, result.output
    assert "run" in result.output
    assert "walk-forward" in result.output


def test_cli_refuses_holdout_before_reading_market_data(tmp_path: Path, monkeypatch) -> None:
    """A holdout run without the explicit flag exits before data access or trial logging."""
    config_dir = tmp_path / "config"
    setup_dir = config_dir / "setups"
    setup_dir.mkdir(parents=True)
    (config_dir / "base.yaml").write_text(
        "app_name: nse-setup-research-engine\ntimezone: Asia/Kolkata\n"
        "log_level: INFO\nrandom_seed: 1\n",
        encoding="utf-8",
    )
    (config_dir / "costs.yaml").write_text(
        "delivery_equity: {brokerage_pct: 0, brokerage_min_per_order: 0, "
        "brokerage_max_per_order: 0, stt_pct_buy: 0, stt_pct_sell: 0, "
        "exchange_txn_pct: 0, sebi_fee_pct: 0, ipft_fee_pct: 0, stamp_duty_pct_buy: 0, "
        "gst_pct: 0, dp_charge_per_sell_scrip: 0}\nslippage_bps: 0\nstress_multipliers: [1.0]\n",
        encoding="utf-8",
    )
    (config_dir / "splits.yaml").write_text(
        "train: {start: 2005-01-01, end: 2017-12-31}\n"
        "validation: {start: 2018-01-01, end: 2022-12-31}\n"
        "holdout: {start: 2023-01-01, end: latest}\n",
        encoding="utf-8",
    )
    (config_dir / "study.yaml").write_text(
        f"horizons: [1]\nentry_limit_buffer_bps: 0\nnotional_rupees: 1000\n"
        f"bootstrap_replicates: 10\nbootstrap_block_length: 2\nfdr_alpha: 0.05\n"
        f"benchmark_symbol: NIFTYBEES\nbenchmark_trend_length: 200\nvolatility_lookback: 20\n"
        f"trial_registry_path: {tmp_path / 'trials.sqlite'}\nevents_dir: {tmp_path / 'events'}\n",
        encoding="utf-8",
    )
    (setup_dir / "pullback_uptrend.yaml").write_text(
        "name: pullback_uptrend\nuniverse: nse_equities\nrationale: test\n"
        "params: {trend_sma_length: 2, rsi_window: 2, rsi_max: 10, traded_value_window: 1}\n"
        "filters: {minimum_price: 1, minimum_average_traded_value: 0}\nexit_rules: {}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NSE_ENGINE_CONFIG_DIR", str(config_dir))

    result = runner.invoke(
        app,
        ["study", "run", "--setup", "pullback_uptrend", "--split", "holdout"],
    )

    assert result.exit_code == 2
    assert "holdout is locked" in result.output
    import sqlite3

    with sqlite3.connect(tmp_path / "trials.sqlite") as connection:
        assert connection.execute("SELECT count(*) FROM trials").fetchone() == (0,)
