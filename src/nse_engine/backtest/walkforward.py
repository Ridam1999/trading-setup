"""Rolling train/test portfolio evaluation with stitched out-of-sample equity."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, timedelta
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.backtest.config import BacktestSettings
from nse_engine.backtest.costs import CostsConfig
from nse_engine.backtest.engine import run_portfolio_backtest
from nse_engine.backtest.metrics import portfolio_metrics
from nse_engine.setups.base import Setup

SetupSelector = Callable[[pd.DataFrame], Sequence[Setup]]


def walk_forward_backtest(
    frame: pd.DataFrame,
    candidate_setups: Sequence[Setup],
    settings: BacktestSettings,
    costs: CostsConfig,
    start_date: date,
    end_date: date,
    train_years: int,
    test_years: int,
    step_years: int,
    selector: SetupSelector | None = None,
    oos_start_date: date | None = None,
    cost_multiplier: float = 1.0,
) -> dict[str, pd.DataFrame]:
    """Select on past-only training windows and stitch non-overlapping test results."""
    if min(train_years, test_years, step_years) < 1:
        raise ValueError("Walk-forward window lengths must be positive")
    if step_years < test_years:
        raise ValueError("Walk-forward step_years must be at least test_years")
    dates = pd.to_datetime(frame["date"])
    first_test_start = oos_start_date or date(start_date.year + train_years, 1, 1)
    if first_test_start <= start_date:
        raise ValueError("First out-of-sample date must follow the available training data")
    windows: list[dict[str, Any]] = []
    equity_parts: list[pd.DataFrame] = []
    trade_parts: list[pd.DataFrame] = []
    capital = settings.initial_capital
    window_index = 0
    test_start = first_test_start
    while True:
        train_start = max(start_date, date(test_start.year - train_years, 1, 1))
        train_end = test_start - timedelta(days=1)
        test_end = date(test_start.year + test_years, 1, 1) - timedelta(days=1)
        if test_start > end_date:
            break
        test_end = min(test_end, end_date)
        train_end = min(train_end, test_start - timedelta(days=1))
        train_mask = (dates.dt.date >= train_start) & (dates.dt.date <= train_end)
        test_mask = (dates.dt.date >= test_start) & (dates.dt.date <= test_end)
        if not test_mask.any():
            break
        training = frame.loc[train_mask].copy()
        if selector is not None:
            selected = list(selector(training))
        else:
            train_scores: list[tuple[float, str, Setup]] = []
            for setup in candidate_setups:
                if training.empty:
                    continue
                train_simulation = run_portfolio_backtest(
                    training,
                    [setup],
                    settings,
                    costs,
                    train_start,
                    train_end,
                    cost_multiplier,
                )
                train_metrics = portfolio_metrics(
                    train_simulation["equity"],
                    train_simulation["trades"],
                    settings.annual_risk_free_rate,
                )
                score = float(train_metrics.get("sharpe", float("-inf")))
                train_scores.append((score, setup.config.name, setup))
            selected = (
                [sorted(train_scores, key=lambda item: (-item[0], item[1]))[0][2]]
                if train_scores
                else []
            )
        if not selected:
            raise ValueError(f"No setup was selected from training window ending {train_end}")
        window_settings = settings.model_copy(update={"initial_capital": capital})
        result = run_portfolio_backtest(
            frame,
            selected,
            window_settings,
            costs,
            test_start,
            test_end,
            cost_multiplier,
        )
        equity = result["equity"].copy()
        if equity.empty:
            test_start = date(test_start.year + step_years, test_start.month, test_start.day)
            continue
        equity["window"] = window_index
        equity_parts.append(equity)
        if not result["trades"].empty:
            trades = result["trades"].copy()
            trades["window"] = window_index
            trade_parts.append(trades)
        metrics = portfolio_metrics(
            result["equity"], result["trades"], settings.annual_risk_free_rate
        )
        windows.append(
            {
                "window": window_index,
                "train_start": train_start,
                "train_end": train_end,
                "test_start": test_start,
                "test_end": test_end,
                "selected_setups": [setup.config.name for setup in selected],
                "selected_parameters": [setup.config.model_dump(mode="json") for setup in selected],
                "capital_start": capital,
                "capital_end": float(equity.iloc[-1]["equity"]),
                **metrics,
            }
        )
        capital = float(equity.iloc[-1]["equity"])
        test_start = date(test_start.year + step_years, test_start.month, test_start.day)
        window_index += 1
    return {
        "equity": pd.concat(equity_parts, ignore_index=True) if equity_parts else pd.DataFrame(),
        "trades": pd.concat(trade_parts, ignore_index=True) if trade_parts else pd.DataFrame(),
        "windows": pd.DataFrame(windows),
    }
