"""Offline tests for the frozen cross-sectional factor study engine."""

from __future__ import annotations

import hashlib
import math
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import pytest
import yaml
from typer.testing import CliRunner

from nse_engine.backtest.costs import load_costs_config
from nse_engine.cli import app
from nse_engine.factor import study as study_module
from nse_engine.factor.config import load_factor_config, load_factor_data_config
from nse_engine.factor.study import (
    DISCLAIMER,
    build_period_returns,
    compute_factor_signals,
    run_factor_study,
)
from nse_engine.study.config import load_gate_settings, load_study_settings
from nse_engine.study.trials import TrialRegistry

CONFIG_DIR = Path("config")
PREREG_PATH = Path("docs/FACTOR_STUDY_PREREG.md")
EQUITIES = ("AAA", "BBB", "CCC")
ALL_SYMBOLS = (*EQUITIES, "NIFTYBEES")
RUNNER = CliRunner()


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    """Write one YAML document from a plain dictionary."""
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _config_dir(tmp_path: Path, registry_path: Path) -> tuple[Path, Path]:
    """Create an isolated config tree with a matching synthetic pre-registration."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    prereg = tmp_path / "factor_prereg.md"
    prereg.write_text("Synthetic frozen factor pre-registration.\n", encoding="utf-8")

    factor = load_factor_config(CONFIG_DIR).model_dump()
    factor["prereg_sha256"] = hashlib.sha256(prereg.read_bytes()).hexdigest()
    _write_yaml(config_dir / "factor.yaml", factor)
    _write_yaml(
        config_dir / "factor_data.yaml",
        {
            "pit_prices_dir": str(tmp_path / "prices"),
            "announcement_index_path": str(tmp_path / "announcement_index.parquet"),
            "fundamentals_path": str(tmp_path / "fundamentals.parquet"),
            "membership_path": str(tmp_path / "membership.parquet"),
            "report_path": str(tmp_path / "factor_report.html"),
        },
    )
    _write_yaml(
        config_dir / "base.yaml",
        {
            "app_name": "nse-setup-research-engine",
            "timezone": "Asia/Kolkata",
            "log_level": "INFO",
            "random_seed": 7,
        },
    )
    _write_yaml(
        config_dir / "study.yaml",
        {
            "horizons": [1],
            "entry_limit_buffer_bps": 100,
            "notional_rupees": 100000,
            "bootstrap_replicates": 10,
            "bootstrap_block_length": 5,
            "fdr_alpha": 0.05,
            "benchmark_symbol": "NIFTYBEES",
            "benchmark_trend_length": 20,
            "volatility_lookback": 20,
            "trial_registry_path": str(registry_path),
            "events_dir": str(tmp_path / "events"),
        },
    )
    _write_yaml(config_dir / "gates.yaml", load_gate_settings(CONFIG_DIR).model_dump())
    shutil.copyfile(CONFIG_DIR / "costs.yaml", config_dir / "costs.yaml")
    _write_yaml(
        config_dir / "splits.yaml",
        {
            "train": {"start": date(2019, 1, 1), "end": date(2020, 6, 30)},
            "validation": {"start": date(2020, 7, 1), "end": date(2021, 12, 31)},
            "holdout": {"start": date(2022, 1, 1), "end": "latest"},
        },
    )
    _write_yaml(config_dir / "research.yaml", {"data_bias_status": "SURVIVOR_BIASED"})
    (tmp_path / "prices").mkdir(parents=True, exist_ok=True)
    return config_dir, prereg


def _price_frame() -> pd.DataFrame:
    """Build synthetic past-only OHLCV rows, including the benchmark ETF."""
    dates = [value.date() for value in pd.date_range("2018-01-01", "2023-12-31", freq="B")]
    rows: list[dict[str, Any]] = []
    for index, day in enumerate(dates):
        for symbol_index, symbol in enumerate(ALL_SYMBOLS):
            close = (
                100.0
                + symbol_index * 10.0
                + index * 0.02
                + 0.5 * math.sin(index / 20.0 + symbol_index)
            )
            open_price = close - 0.05
            rows.append(
                {
                    "symbol": symbol,
                    "date": day,
                    "open": open_price,
                    "high": close + 0.25,
                    "low": open_price - 0.25,
                    "close": close,
                    "volume": 1_000_000 + symbol_index,
                    "source": "synthetic_pit",
                    "ingested_at": "2026-11-18T00:00:00+00:00",
                }
            )
    return pd.DataFrame.from_records(rows)


def _membership_frame(prices: pd.DataFrame) -> pd.DataFrame:
    """Build month-end point-in-time membership for the three equities."""
    sessions = sorted(set(prices["date"]))
    dates: list[date] = []
    for year, month in sorted({(day.year, day.month) for day in sessions}):
        candidates = [day for day in sessions if day.year == year and day.month == month]
        if date(year, month, 1) >= date(2019, 1, 1) and candidates[-1] <= date(2022, 12, 31):
            dates.append(candidates[-1])
    rows = [
        {
            "rebalance_date": day,
            "symbol": symbol,
            "rank": rank,
            "traded_value": 1_000_000.0 - rank,
            "index_name": "pit_traded_value_top_n",
            "membership_type": "point_in_time_delisted_inclusive",
        }
        for day in dates
        for rank, symbol in enumerate(EQUITIES, start=1)
    ]
    return pd.DataFrame.from_records(rows)


def _fundamentals_frame() -> pd.DataFrame:
    """Build announcement-dated rows that vary by symbol and month."""
    announcements = [value.date() for value in pd.date_range("2018-01-01", "2023-12-31", freq="MS")]
    rows: list[dict[str, Any]] = []
    for month_index, announced in enumerate(announcements):
        for symbol_index, symbol in enumerate(EQUITIES):
            rows.extend(
                [
                    {
                        "symbol": symbol,
                        "announcement_date": announced,
                        "field": "trailing_12m_diluted_eps",
                        "value": 5.0 + symbol_index * 2.0 + (month_index % 6) * 0.5,
                    },
                    {
                        "symbol": symbol,
                        "announcement_date": announced,
                        "field": "trailing_12m_net_profit",
                        "value": 100_000_000.0 * (1 + symbol_index + (month_index % 6) * 0.1),
                    },
                    {
                        "symbol": symbol,
                        "announcement_date": announced,
                        "field": "average_shareholders_equity",
                        "value": 1_000_000_000.0 * (1 + symbol_index),
                    },
                ]
            )
    return pd.DataFrame.from_records(rows)


def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Create a full synthetic environment and patch the PIT price loader."""
    registry_path = tmp_path / "trials.sqlite"
    config_dir, prereg = _config_dir(tmp_path, registry_path)
    prices = _price_frame()
    membership = _membership_frame(prices)
    fundamentals = _fundamentals_frame()
    membership.to_parquet(tmp_path / "membership.parquet", index=False)
    fundamentals.to_parquet(tmp_path / "fundamentals.parquet", index=False)
    monkeypatch.setattr(study_module, "load_pit_bars", lambda directory: prices.copy())

    return {
        "config_dir": config_dir,
        "prereg": prereg,
        "prices": prices,
        "membership": membership,
        "fundamentals": fundamentals,
        "factor": load_factor_config(config_dir),
        "data": load_factor_data_config(config_dir),
        "study": load_study_settings(config_dir),
        "gates": load_gate_settings(config_dir),
        "costs": load_costs_config(config_dir),
        "registry": TrialRegistry(registry_path),
        "registry_path": registry_path,
    }


def _prepared(env: dict[str, Any]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return frames with the private date columns used by the engine."""
    prices = env["prices"].assign(_date=pd.to_datetime(env["prices"]["date"]).dt.date)
    membership = env["membership"].assign(
        _rebalance_date=pd.to_datetime(env["membership"]["rebalance_date"]).dt.date
    )
    fundamentals = env["fundamentals"].assign(
        _announced=pd.to_datetime(env["fundamentals"]["announcement_date"]).dt.date
    )
    return prices, membership, fundamentals


def test_default_run_is_train_validation_only_and_appends_every_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A default run never reads or logs holdout, and logs each result once."""
    env = _environment(tmp_path, monkeypatch)
    output = run_factor_study(
        env["factor"],
        env["data"],
        env["study"],
        env["gates"],
        env["costs"],
        env["registry"],
        env["prereg"],
        config_directory=env["config_dir"],
    )
    rows = env["registry"].rows(include_unlocks=True)

    assert output.results
    assert {result.split for result in output.results} == {"train", "validation"}
    assert all(not row["split"] == "holdout" for row in rows)
    assert all(row["setup_name"] != "__holdout_unlock__" for row in rows)
    assert len(rows) == output.trials_appended == len(output.results)
    assert all(result.adjusted_p_value >= 0 for result in output.results)


def test_lowered_cost_stress_floor_blocks_before_any_trial_is_appended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A config that weakens the locked cost stress cannot create registry rows."""
    env = _environment(tmp_path / "stress", monkeypatch)
    lowered = env["factor"].model_copy(update={"cost_stress_multiplier": 1.0})

    with pytest.raises(ValueError, match="cost stress is below the locked gate floor"):
        run_factor_study(
            lowered,
            env["data"],
            env["study"],
            env["gates"],
            env["costs"],
            env["registry"],
            env["prereg"],
            config_directory=env["config_dir"],
        )
    assert env["registry"].rows(include_unlocks=True) == []


def test_signals_and_entries_use_only_past_information(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Later prices and restatements cannot change earlier baskets or fills."""
    env = _environment(tmp_path, monkeypatch)
    prices, membership, fundamentals = _prepared(env)
    dates = [date(2020, 1, 31), date(2020, 2, 28)]
    original = compute_factor_signals(prices, membership, fundamentals, env["factor"], dates)

    spiked_prices = env["prices"].copy()
    future = pd.to_datetime(spiked_prices["date"]).dt.date > date(2021, 1, 1)
    for column in ("open", "high", "low", "close"):
        spiked_prices.loc[future, column] = spiked_prices.loc[future, column] * 10
    spiked = spiked_prices.assign(_date=pd.to_datetime(spiked_prices["date"]).dt.date)
    after_price_spike = compute_factor_signals(
        spiked, membership, fundamentals, env["factor"], dates
    )
    pd.testing.assert_frame_equal(original, after_price_spike)

    restated_rows = pd.concat(
        [
            env["fundamentals"],
            pd.DataFrame.from_records(
                [
                    {
                        "symbol": "AAA",
                        "announcement_date": date(2022, 2, 1),
                        "field": "trailing_12m_diluted_eps",
                        "value": 9999.0,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    restated = restated_rows.assign(
        _announced=pd.to_datetime(restated_rows["announcement_date"]).dt.date
    )
    after_restatement = compute_factor_signals(prices, membership, restated, env["factor"], dates)
    pd.testing.assert_frame_equal(original, after_restatement)

    returns = build_period_returns(
        original,
        prices,
        membership,
        env["study"],
        env["costs"],
        env["factor"].cost_stress_multiplier,
        env["factor"].horizons_months,
        env["factor"].construction.signal_to_order_lag_sessions,
    )
    assert not returns.empty
    assert all(returns["entry_date"] > returns["signal_date"])
    assert all(returns["exit_date"] >= pd.to_datetime(returns["signal_date"]).dt.date)


def test_explicit_holdout_unlock_is_logged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only an explicit flag logs holdout access and reads holdout dates."""
    env = _environment(tmp_path / "holdout", monkeypatch)
    output = run_factor_study(
        env["factor"],
        env["data"],
        env["study"],
        env["gates"],
        env["costs"],
        env["registry"],
        env["prereg"],
        config_directory=env["config_dir"],
        include_holdout=True,
    )
    rows = env["registry"].rows(include_unlocks=True)
    unlocks = [row for row in rows if row["setup_name"] == "__holdout_unlock__"]
    holdout_rows = [row for row in rows if row["split"] == "holdout"]

    assert "holdout" in {result.split for result in output.results}
    assert len(unlocks) == 1
    assert holdout_rows
    assert all(row["setup_name"] != "__holdout_unlock__" for row in env["registry"].rows())


def test_factor_run_cli_blocks_without_pit_inputs() -> None:
    """The checked-in configuration still refuses to run real data."""
    result = RUNNER.invoke(app, ["factor", "run"])

    assert result.exit_code == 1
    assert "FACTOR STUDY BLOCKED" in result.output
    assert DISCLAIMER in result.output


def test_factor_run_cli_writes_candidate_report_without_approvals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI writes a candidate report and leaves approval files untouched."""
    env = _environment(tmp_path, monkeypatch)
    monkeypatch.setenv("NSE_ENGINE_CONFIG_DIR", str(env["config_dir"]))
    approval_path = env["config_dir"] / "approved_setups.yaml"
    sentinel = "approved_setups:\n  - setup: must_survive\n"
    approval_path.write_text(sentinel, encoding="utf-8")

    result = RUNNER.invoke(
        app,
        [
            "factor",
            "run",
            "--directory",
            str(env["config_dir"]),
            "--prereg",
            str(env["prereg"]),
        ],
    )
    report_path = tmp_path / "factor_report.html"

    assert result.exit_code == 0, result.output
    assert report_path.is_file()
    document = report_path.read_text(encoding="utf-8")
    assert DISCLAIMER in document
    assert "SURVIVOR_BIASED" in document
    assert env["factor"].prereg_sha256 in document
    assert "NOT APPROVAL EVIDENCE" in document
    assert approval_path.read_text(encoding="utf-8") == sentinel
    assert '"holdout_accessed": false' in result.output
