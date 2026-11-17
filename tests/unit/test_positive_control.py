"""Isolation and behavior tests for the deliberately invalid look-ahead control."""

import json
from pathlib import Path

import pandas as pd

from nse_engine.cli import _eligible_report_rows
from nse_engine.setups.base import SetupConfig, load_setup_config
from nse_engine.setups.registry import SETUP_REGISTRY
from nse_engine.study.config import load_gate_settings
from nse_engine.study.control import LookAheadControl, summarize_control_results
from nse_engine.study.trials import TrialRegistry


def _control_config() -> SetupConfig:
    """Load the declared control configuration."""
    return load_setup_config("__control_lookahead__", Path("config/controls"))


def test_control_is_not_a_registered_setup() -> None:
    """The leaking rule cannot be reached through the research setup registry."""
    assert LookAheadControl.name not in SETUP_REGISTRY


def test_control_signal_deliberately_uses_tomorrow() -> None:
    """The control reads the next close so a known edge is guaranteed."""
    control = LookAheadControl(_control_config())
    frame = pd.DataFrame({"close": [100.0, 101.0, 102.0, 103.0]})

    assert control.signals(frame).tolist() == [True, True, True, False]

    altered = frame.copy()
    altered.loc[2, "close"] = 50.0
    assert not control.signals(altered).iloc[1]


def test_control_summary_uses_only_control_trials() -> None:
    """Non-control rows cannot enter the control's own FDR family."""
    metrics = {
        "control": True,
        "provisional": True,
        "n_independent_dates": 100,
        "net_return": {"n_events": 500, "n_independent_dates": 100, "expectancy": 0.05},
        "net_return_2x_cost": {"expectancy": 0.04},
        "excess_over_baseline": {"date_clustered_p": 1e-20},
        "yearly": [
            {"year": 2020, "events": 100, "expectancy": 0.05, "total_profit": 5.0},
            {"year": 2021, "events": 100, "expectancy": 0.05, "total_profit": 5.0},
            {"year": 2022, "events": 100, "expectancy": 0.05, "total_profit": 5.0},
        ],
        "breadth": 0.8,
    }
    rows = [
        {
            "params_hash": "control",
            "split": split,
            "horizon": 1,
            "metrics_json": json.dumps(metrics),
        }
        for split in ("train", "validation")
    ]
    rows.append(
        {
            "params_hash": "research",
            "split": "validation",
            "horizon": 1,
            "metrics_json": json.dumps(
                {"provisional": True, "excess_over_baseline": {"date_clustered_p": 0.9}}
            ),
        }
    )

    summary = summarize_control_results(rows, load_gate_settings())

    assert summary["pass"] is True
    assert summary["adjusted_p_range"] == [1e-20, 1e-20]
    assert all(group["verdict"] == "control_pass" for group in summary["groups"])


def test_report_rows_exclude_control_trials(tmp_path: Path) -> None:
    """A control-tagged row never reaches FDR totals, reports, or approvals."""
    registry = TrialRegistry(tmp_path / "trials.sqlite")
    config = _control_config()
    registry.append_trial(
        config,
        "train",
        1,
        {"control": True, "provisional": True, "excess_over_baseline": {"date_clustered_p": 1e-9}},
    )
    registry.append_trial(
        config,
        "train",
        1,
        {"provisional": True, "excess_over_baseline": {"date_clustered_p": 0.5}},
    )

    rows = _eligible_report_rows(registry, True)

    assert len(rows) == 1
    assert json.loads(rows[0]["metrics_json"])["excess_over_baseline"]["date_clustered_p"] == 0.5
