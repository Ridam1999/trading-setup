"""Approval-gate pass/fail examples."""

import pandas as pd

from nse_engine.study.config import load_gate_settings
from nse_engine.study.gates import evaluate_provisional_gates, evaluate_setup_gates


def _split_metrics(expectancy: float) -> dict[str, dict[str, float | int]]:
    """Build complete split metrics at the configured 2x cost stress."""
    return {
        split: {
            "n_independent_dates": 50,
            "net_return_2x_cost": {"expectancy": expectancy},
        }
        for split in ("train", "validation", "holdout")
    }


def _yearly() -> pd.DataFrame:
    """Build five evenly profitable calendar years."""
    return pd.DataFrame(
        {
            "year": [2018, 2019, 2020, 2021, 2022],
            "events": [30] * 5,
            "expectancy": [0.01] * 5,
            "total_profit": [10.0] * 5,
        }
    )


def test_all_validation_gates_can_pass() -> None:
    """A broad, significant, stable result can be approved."""
    result = evaluate_setup_gates(
        _split_metrics(0.01),
        0.01,
        _yearly(),
        0.7,
        [0.01, 0.02],
        "Economic rationale.",
        load_gate_settings(),
    )

    assert result.approved
    assert all(check["passed"] for check in result.checks.values())


def test_noise_result_is_rejected_with_reasons() -> None:
    """Negative cost-stressed expectancy and weak significance cannot pass."""
    result = evaluate_setup_gates(
        _split_metrics(-0.01),
        0.8,
        _yearly(),
        0.4,
        [-0.01, 0.0],
        "Economic rationale.",
        load_gate_settings(),
    )

    assert not result.approved
    assert not result.checks["positive_expectancy_holdout_net_return_2x_cost"]["passed"]
    assert not result.checks["multiple_testing_adjusted_significance"]["passed"]


def test_survivor_biased_data_only_allows_provisional_pass() -> None:
    """Train and validation passes are provisional while holdout is still locked."""
    metrics = _split_metrics(0.01)
    metrics.pop("holdout")
    result = evaluate_setup_gates(
        metrics,
        0.01,
        _yearly(),
        0.7,
        [0.01, 0.02],
        "Economic rationale.",
        load_gate_settings(),
        data_bias_status="SURVIVOR_BIASED",
    )

    assert result.provisional_pass
    assert not result.approved


def test_provisional_gates_never_approve() -> None:
    """Even perfect kill-stage metrics cannot approve, and holdout is not a failed gate."""
    metrics = _split_metrics(0.01)
    metrics["holdout"]["net_return_2x_cost"] = {"expectancy": -0.01}

    result = evaluate_provisional_gates(
        metrics,
        0.01,
        _yearly(),
        0.7,
        [0.01, 0.02],
        "Economic rationale.",
        load_gate_settings(),
    )

    assert result.provisional_pass
    assert not result.approved
    assert not any(name.startswith("positive_expectancy_holdout_") for name in result.checks)
