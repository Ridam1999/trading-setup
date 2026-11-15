"""Configurable gates that keep an experiment rejected until every check passes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.study.config import GateSettings


@dataclass(frozen=True)
class GateResult:
    """Approval result and named gate outcomes."""

    approved: bool
    checks: dict[str, dict[str, Any]]


def evaluate_setup_gates(
    split_metrics: dict[str, dict[str, Any]],
    adjusted_p_value: float,
    yearly: pd.DataFrame,
    breadth: float,
    neighbour_expectancies: list[float],
    rationale: str,
    settings: GateSettings,
) -> GateResult:
    """Evaluate all configured sample-size, return, significance, breadth, and stability gates."""
    checks: dict[str, dict[str, Any]] = {}

    def add(name: str, passed: bool, observed: Any, threshold: Any, reason: str) -> None:
        """Record one gate and a user-readable reason."""
        checks[name] = {
            "passed": bool(passed),
            "observed": observed,
            "threshold": threshold,
            "reason": reason if passed else f"Failed: {reason}",
        }

    for split_name in ("train", "validation"):
        metrics = split_metrics.get(split_name)
        count = int(metrics.get("n_independent_dates", 0)) if metrics else 0
        add(
            f"minimum_dates_{split_name}",
            count >= settings.minimum_independent_dates,
            count,
            settings.minimum_independent_dates,
            f"{split_name} has enough independent signal dates",
        )

    stress_key = f"net_return_{settings.minimum_cost_stress_multiplier:g}x_cost"
    for split_name in ("train", "validation", "holdout"):
        metrics = split_metrics.get(split_name)
        stress_summary = metrics.get(stress_key, {}) if metrics else {}
        expectancy = stress_summary.get("expectancy")
        passed = isinstance(expectancy, int | float) and expectancy > 0
        add(
            f"positive_expectancy_{split_name}_{stress_key}",
            passed,
            expectancy,
            "> 0",
            f"{split_name} expectancy remains positive at cost stress",
        )

    add(
        "multiple_testing_adjusted_significance",
        adjusted_p_value <= settings.maximum_adjusted_p_value,
        adjusted_p_value,
        settings.maximum_adjusted_p_value,
        "Excess over baseline is significant after FDR correction",
    )

    if yearly.empty:
        positive_year_fraction = 0.0
        maximum_profit_fraction = 1.0
    else:
        positive_year_fraction = float(yearly["expectancy"].gt(0).mean())
        positive_profit = yearly["total_profit"].clip(lower=0)
        profit_total = float(positive_profit.sum())
        maximum_profit_fraction = (
            float(positive_profit.max() / profit_total) if profit_total > 0 else 1.0
        )
    add(
        "positive_calendar_year_fraction",
        positive_year_fraction >= settings.minimum_positive_year_fraction,
        positive_year_fraction,
        settings.minimum_positive_year_fraction,
        "Enough calendar years have positive expectancy",
    )
    add(
        "positive_symbol_breadth",
        breadth >= settings.minimum_positive_symbol_fraction,
        breadth,
        settings.minimum_positive_symbol_fraction,
        "Enough event symbols have positive expectancy",
    )
    add(
        "single_year_profit_concentration",
        maximum_profit_fraction <= settings.maximum_single_year_profit_fraction,
        maximum_profit_fraction,
        settings.maximum_single_year_profit_fraction,
        "No year dominates positive profits",
    )
    neighbours_pass = bool(neighbour_expectancies) and all(
        value > 0 for value in neighbour_expectancies
    )
    add(
        "parameter_neighbour_stability",
        neighbours_pass if settings.require_positive_parameter_neighbours else True,
        neighbour_expectancies,
        "all declared adjacent parameter values positive",
        "Declared parameter neighbours remain positive",
    )
    has_rationale = bool(rationale.strip())
    add(
        "written_economic_rationale",
        has_rationale,
        has_rationale,
        True,
        "Setup YAML includes an economic rationale",
    )
    return GateResult(
        approved=all(result["passed"] for result in checks.values()),
        checks=checks,
    )
