"""Deliberately invalid look-ahead control used to validate real-data wiring.

This module is intentionally outside ``SETUP_REGISTRY``. Its signal reads the next
session's close, so it can prove that the data, fill, cost, and statistics pipeline
detects a known edge. A pass is a plumbing check, never research evidence.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.setups.base import SetupConfig
from nse_engine.study.config import GateSettings
from nse_engine.study.gates import evaluate_provisional_gates
from nse_engine.study.stats import benjamini_hochberg


class LookAheadControl:
    """Signal when tomorrow's close is higher than today's close.

    This is a deliberate look-ahead violation and must never be registered as a
    research setup or used to approve anything.
    """

    name = "__control_lookahead__"

    def __init__(self, config: SetupConfig) -> None:
        """Store the validated control configuration."""
        self.config = config

    def required_features(self) -> list[str]:
        """List the only price column this deliberately invalid rule reads."""
        return ["close"]

    def signals(self, frame: pd.DataFrame) -> pd.Series:
        """Use tomorrow's close; this is intentionally not a causal signal."""
        return frame["close"].shift(-1).gt(frame["close"])


def summarize_control_results(rows: list[dict[str, Any]], gates: GateSettings) -> dict[str, Any]:
    """Evaluate only control-tagged trials in their own FDR family."""
    control_rows = [row for row in rows if json.loads(row["metrics_json"]).get("control") is True]
    if not control_rows:
        raise ValueError("No control trials were found in the control registry")
    p_values: list[float] = []
    for row in control_rows:
        value = (
            json.loads(row["metrics_json"]).get("excess_over_baseline", {}).get("date_clustered_p")
        )
        p_values.append(float(value) if isinstance(value, int | float) else float("nan"))
    adjusted = benjamini_hochberg(p_values)
    groups: dict[tuple[str, int], list[tuple[dict[str, Any], float]]] = {}
    for row, adjusted_p in zip(control_rows, adjusted, strict=True):
        groups.setdefault((row["params_hash"], row["horizon"]), []).append((row, adjusted_p))
    relaxed_gates = gates.model_copy(update={"require_positive_parameter_neighbours": False})
    checks: dict[str, bool] = {}
    verdicts: list[dict[str, Any]] = []
    for (params_hash, horizon), trials in sorted(groups.items()):
        metrics_by_split = {
            trial["split"]: json.loads(trial["metrics_json"]) for trial, _ in trials
        }
        validation = [adjusted_p for trial, adjusted_p in trials if trial["split"] == "validation"]
        adjusted_p = validation[0] if validation else float("nan")
        yearly_parts = [
            pd.DataFrame(metrics["yearly"])
            for metrics in metrics_by_split.values()
            if metrics.get("yearly")
        ]
        yearly = (
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
        breadth = [
            float(metrics["breadth"])
            for metrics in metrics_by_split.values()
            if isinstance(metrics.get("breadth"), int | float)
        ]
        result = evaluate_provisional_gates(
            metrics_by_split,
            adjusted_p,
            yearly,
            min(breadth) if breadth else 0.0,
            [],
            "POSITIVE CONTROL: deliberately invalid look-ahead signal.",
            relaxed_gates,
        )
        for name, check in result.checks.items():
            checks[name] = checks.get(name, True) and bool(check["passed"])
        verdicts.append(
            {
                "params_hash": params_hash,
                "horizon": horizon,
                "adjusted_p_value": adjusted_p,
                "verdict": "control_pass" if result.provisional_pass else "control_fail",
            }
        )
    return {
        "pass": all(checks.values())
        and all(item["verdict"] == "control_pass" for item in verdicts),
        "gate_checks": checks,
        "groups": verdicts,
        "adjusted_p_range": [min(adjusted), max(adjusted)],
    }
