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
    monkeypatch.setenv("NSE_ENGINE_CONFIG_DIR", str(config_dir))
    fixture = Path("tests/fixtures/eod2_sample.csv").resolve()

    imported = runner.invoke(app, ["data", "import-eod2", "--path", str(fixture)])
    assert imported.exit_code == 0, imported.output
    bhavcopy = Path("tests/fixtures/bhavcopy_legacy_sample.csv").resolve()
    updated = runner.invoke(app, ["data", "update", "--path", str(bhavcopy)])
    assert updated.exit_code == 0, updated.output
    checked = runner.invoke(app, ["data", "check"])
    assert checked.exit_code == 0, checked.output
    summary = runner.invoke(app, ["data", "summary"])

    assert summary.exit_code == 0, summary.output
    assert '"rows": 4' in summary.output
    report = tmp_path / "reports" / "data_health.html"
    assert report.is_file()
    assert "Static universe; survivorship bias likely" in report.read_text(encoding="utf-8")
    assert (tmp_path / "adjusted" / "calendar" / "year=2024" / "calendar.parquet").is_file()
    assert (
        tmp_path / "adjusted" / "universe_membership" / "year=unknown" / "membership.parquet"
    ).is_file()
