"""Date-clustered statistics, FDR correction, and block bootstrap helpers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
from scipy.stats import norm, t  # type: ignore[import-untyped]


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """Return Benjamini-Hochberg adjusted p-values in their original order."""
    values = np.asarray(p_values, dtype=float)
    result = np.full(values.shape, np.nan, dtype=float)
    valid = np.isfinite(values)
    if not valid.any():
        return result.tolist()  # type: ignore[no-any-return]
    selected = values[valid]
    if ((selected < 0) | (selected > 1)).any():
        raise ValueError("P-values must be in [0, 1]")
    order = np.argsort(selected)
    ranked = selected[order]
    count = len(ranked)
    adjusted_sorted = np.minimum.accumulate((ranked * count / np.arange(1, count + 1))[::-1])[::-1]
    adjusted = np.empty(count, dtype=float)
    adjusted[order] = np.minimum(adjusted_sorted, 1.0)
    result[valid] = adjusted
    return result.tolist()  # type: ignore[no-any-return]


def date_clustered_t_statistic(
    frame: pd.DataFrame, value_column: str = "excess_return"
) -> tuple[float, float, int]:
    """Average outcomes within date, then run a one-sample t-test across dates."""
    date_means = frame.groupby("signal_date", sort=True)[value_column].mean().dropna()
    count = len(date_means)
    if count < 2:
        return float("nan"), float("nan"), count
    standard_error = date_means.std(ddof=1) / np.sqrt(count)
    if standard_error == 0:
        if date_means.mean() > 0:
            return float("inf"), 0.0, count
        if date_means.mean() < 0:
            return float("-inf"), 0.0, count
        return 0.0, 1.0, count
    statistic = float(date_means.mean() / standard_error)
    p_value = float(2 * t.sf(abs(statistic), df=count - 1))
    return statistic, p_value, count


def newey_west_t_statistic(values: Sequence[float], max_lag: int) -> float:
    """Compute a Newey-West HAC t-statistic for a sequence of date-level means."""
    series = np.asarray(values, dtype=float)
    series = series[np.isfinite(series)]
    count = len(series)
    if count < 2:
        return float("nan")
    if max_lag < 0:
        raise ValueError("Newey-West lag must be non-negative")
    centered = series - series.mean()
    long_run_variance = float(np.dot(centered, centered) / count)
    for lag in range(1, min(max_lag, count - 1) + 1):
        autocovariance = float(np.dot(centered[lag:], centered[:-lag]) / count)
        bartlett_weight = 1 - lag / (max_lag + 1)
        long_run_variance += 2 * bartlett_weight * autocovariance
    standard_error = np.sqrt(max(long_run_variance, 0) / count)
    if standard_error == 0:
        return float("inf") if series.mean() > 0 else 0.0
    return float(series.mean() / standard_error)


def block_bootstrap_expectancy(
    frame: pd.DataFrame,
    value_column: str,
    replicates: int,
    block_length: int,
    seed: int,
    confidence: float,
) -> tuple[float, float]:
    """Estimate a date-block bootstrap interval for expectancy."""
    if replicates < 1 or block_length < 1 or not 0 < confidence < 1:
        raise ValueError("Bootstrap settings must be positive and confidence must be in (0, 1)")
    date_means = frame.groupby("signal_date", sort=True)[value_column].mean().dropna().to_numpy()
    if not len(date_means):
        return float("nan"), float("nan")
    block = min(block_length, len(date_means))
    rng = np.random.default_rng(seed)
    samples = np.empty(replicates, dtype=float)
    possible_starts = np.arange(len(date_means) - block + 1)
    blocks_needed = int(np.ceil(len(date_means) / block))
    for iteration in range(replicates):
        starts = rng.choice(possible_starts, size=blocks_needed, replace=True)
        sample = np.concatenate([date_means[start : start + block] for start in starts])[
            : len(date_means)
        ]
        samples[iteration] = sample.mean()
    tail = (1 - confidence) / 2
    return float(np.quantile(samples, tail)), float(np.quantile(samples, 1 - tail))


def return_summary(frame: pd.DataFrame, value_column: str = "net_return") -> dict[str, Any]:
    """Compute descriptive and date-clustered metrics for event returns."""
    returns = pd.to_numeric(frame[value_column], errors="coerce").dropna()
    if returns.empty:
        return {"n_events": 0, "n_independent_dates": 0}
    statistic, p_value, independent_dates = date_clustered_t_statistic(frame, value_column)
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    average_win = float(wins.mean()) if not wins.empty else 0.0
    average_loss = float(losses.mean()) if not losses.empty else 0.0
    date_means = frame.groupby("signal_date", sort=True)[value_column].mean().dropna()
    horizon = int(frame["horizon"].iloc[0]) if "horizon" in frame and len(frame) else 1
    return {
        "n_events": int(len(returns)),
        "n_independent_dates": int(independent_dates),
        "mean": float(returns.mean()),
        "median": float(returns.median()),
        "standard_deviation": float(returns.std(ddof=1)) if len(returns) > 1 else 0.0,
        "hit_rate": float((returns > 0).mean()),
        "average_win": average_win,
        "average_loss": average_loss,
        "payoff_ratio": abs(average_win / average_loss) if average_loss else float("inf"),
        "expectancy": float(returns.mean()),
        "percentile_1": float(returns.quantile(0.01)),
        "percentile_5": float(returns.quantile(0.05)),
        "worst_loss": float(returns.min()),
        "date_clustered_t": statistic,
        "date_clustered_p": p_value,
        "newey_west_t": newey_west_t_statistic(date_means.to_numpy(), max(horizon - 1, 0)),
    }


def yearly_returns(frame: pd.DataFrame, value_column: str = "net_return") -> pd.DataFrame:
    """Summarize event expectancy, event count, and total profit by signal year."""
    if frame.empty:
        return pd.DataFrame(columns=["year", "events", "expectancy", "total_profit"])
    prepared = frame.assign(year=pd.to_datetime(frame["signal_date"]).dt.year)
    return (
        prepared.groupby("year", sort=True)[value_column]
        .agg(events="size", expectancy="mean", total_profit="sum")
        .reset_index()
    )


def symbol_breadth(frame: pd.DataFrame, value_column: str = "net_return") -> float:
    """Return the fraction of event symbols with positive mean return."""
    if frame.empty:
        return 0.0
    per_symbol = frame.groupby("symbol")[value_column].mean()
    return float(per_symbol.gt(0).mean()) if len(per_symbol) else 0.0


def approximate_deflated_sharpe_probability(
    sharpe: float, observations: int, trial_count: int
) -> float:
    """Approximate probability a Sharpe exceeds the expected null maximum across trials."""
    if observations < 1 or trial_count < 1:
        raise ValueError("Observation and trial counts must be positive")
    expected_maximum = float(norm.ppf(1 - 1 / max(trial_count + 1, 2))) / np.sqrt(observations)
    return float(norm.cdf((sharpe - expected_maximum) * np.sqrt(observations)))
