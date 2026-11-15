"""Robustness analysis around the daily portfolio simulator."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from nse_engine.backtest.config import BacktestSettings
from nse_engine.backtest.costs import CostsConfig
from nse_engine.backtest.engine import run_portfolio_backtest
from nse_engine.backtest.metrics import portfolio_metrics
from nse_engine.setups.base import Setup


def cost_stress_runs(
    frame: pd.DataFrame,
    setups: Sequence[Setup],
    settings: BacktestSettings,
    costs: CostsConfig,
    start_date: date,
    end_date: date,
) -> dict[str, dict[str, Any]]:
    """Rerun a portfolio at every configured cost and slippage multiplier."""
    results: dict[str, dict[str, Any]] = {}
    for multiplier in costs.stress_multipliers:
        simulation = run_portfolio_backtest(
            frame, setups, settings, costs, start_date, end_date, multiplier
        )
        key = f"{multiplier:g}x"
        results[key] = portfolio_metrics(
            simulation["equity"], simulation["trades"], settings.annual_risk_free_rate
        )
    return results


def monte_carlo_drawdowns(
    trades: pd.DataFrame,
    replicates: int,
    seed: int,
) -> dict[str, float | int]:
    """Bootstrap trade returns with replacement and summarize maximum drawdowns."""
    if replicates < 1:
        raise ValueError("Monte Carlo replicate count must be positive")
    if trades.empty or "net_return" not in trades:
        return {
            "replicates": replicates,
            "drawdown_p05": 0.0,
            "drawdown_median": 0.0,
            "drawdown_p95": 0.0,
        }
    returns = pd.to_numeric(trades["net_return"], errors="coerce").dropna().to_numpy(dtype=float)
    if not len(returns):
        return {
            "replicates": replicates,
            "drawdown_p05": 0.0,
            "drawdown_median": 0.0,
            "drawdown_p95": 0.0,
        }
    rng = np.random.default_rng(seed)
    drawdowns = np.empty(replicates, dtype=float)
    for replicate in range(replicates):
        sample = rng.choice(returns, size=len(returns), replace=True)
        curve = np.cumprod(1 + sample)
        peaks = np.maximum.accumulate(np.concatenate(([1.0], curve)))[1:]
        drawdowns[replicate] = np.min(curve / peaks - 1)
    return {
        "replicates": replicates,
        "drawdown_p05": float(np.quantile(drawdowns, 0.05)),
        "drawdown_median": float(np.quantile(drawdowns, 0.5)),
        "drawdown_p95": float(np.quantile(drawdowns, 0.95)),
    }
