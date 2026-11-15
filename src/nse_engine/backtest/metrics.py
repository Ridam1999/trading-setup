"""Performance, risk, and benchmark metrics for portfolio results."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd  # type: ignore[import-untyped]


def portfolio_metrics(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    annual_risk_free_rate: float,
) -> dict[str, Any]:
    """Calculate return, risk, drawdown, exposure, and trade-quality statistics."""
    if equity_curve.empty:
        return {"n_sessions": 0, "n_trades": len(trades)}
    equity = pd.to_numeric(equity_curve["equity"], errors="coerce").dropna()
    daily_returns = equity.pct_change().dropna()
    session_count = len(equity)
    years = max((session_count - 1) / 252, 1 / 252)
    total_return = float(equity.iloc[-1] / equity.iloc[0] - 1)
    cagr = (
        float((equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1)
        if equity.iloc[0] > 0
        else float("nan")
    )
    annual_volatility = (
        float(daily_returns.std(ddof=1) * np.sqrt(252)) if len(daily_returns) > 1 else 0.0
    )
    daily_rf = (1 + annual_risk_free_rate) ** (1 / 252) - 1
    excess = daily_returns - daily_rf
    standard_deviation = float(daily_returns.std(ddof=1)) if len(daily_returns) > 1 else 0.0
    sharpe = (
        float(excess.mean() / standard_deviation * np.sqrt(252)) if standard_deviation > 0 else 0.0
    )
    downside = np.minimum(daily_returns.to_numpy() - daily_rf, 0)
    downside_deviation = (
        float(np.sqrt(np.mean(downside**2)) * np.sqrt(252)) if len(downside) else 0.0
    )
    sortino = (
        float((daily_returns.mean() * 252 - annual_risk_free_rate) / downside_deviation)
        if downside_deviation > 0
        else 0.0
    )
    peaks = equity.cummax()
    drawdowns = equity / peaks - 1
    maximum_drawdown = float(drawdowns.min())
    durations: list[int] = []
    current_duration = 0
    for is_underwater in drawdowns.lt(0):
        current_duration = current_duration + 1 if is_underwater else 0
        durations.append(current_duration)
    calmar = cagr / abs(maximum_drawdown) if maximum_drawdown < 0 else 0.0

    trade_returns = (
        pd.to_numeric(trades["net_return"], errors="coerce").dropna()
        if not trades.empty and "net_return" in trades
        else pd.Series(dtype=float)
    )
    wins = trade_returns[trade_returns > 0]
    losses = trade_returns[trade_returns < 0]
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    avg_win = float(wins.mean()) if len(wins) else 0.0
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    payoff = avg_win / abs(avg_loss) if avg_loss else float("inf") if avg_win else 0.0
    expectancy = float(trade_returns.mean()) if len(trade_returns) else 0.0
    profit_factor = (
        gross_profit / gross_loss if gross_loss else float("inf") if gross_profit else 0.0
    )

    benchmark = pd.to_numeric(equity_curve["benchmark_close"], errors="coerce").dropna()
    benchmark_return = (
        float(benchmark.iloc[-1] / benchmark.iloc[0] - 1) if len(benchmark) > 1 else float("nan")
    )
    exposure = pd.to_numeric(equity_curve["exposure_fraction"], errors="coerce")
    turnover = (
        float(
            trades["entry_price"].mul(trades["quantity"]).sum()
            + trades["exit_price"].mul(trades["quantity"]).sum()
        )
        if not trades.empty
        else 0.0
    )
    return {
        "n_sessions": session_count,
        "n_trades": len(trade_returns),
        "total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": annual_volatility,
        "sharpe": sharpe,
        "sortino": sortino,
        "maximum_drawdown": maximum_drawdown,
        "maximum_drawdown_duration_sessions": max(durations, default=0),
        "calmar": calmar,
        "average_exposure": float(exposure.mean()) if len(exposure) else 0.0,
        "turnover_rupees": turnover,
        "win_rate": float((trade_returns > 0).mean()) if len(trade_returns) else 0.0,
        "average_win": avg_win,
        "average_loss": avg_loss,
        "payoff_ratio": payoff,
        "expectancy": expectancy,
        "profit_factor": profit_factor,
        "best_trade": float(trade_returns.max()) if len(trade_returns) else 0.0,
        "worst_trade": float(trade_returns.min()) if len(trade_returns) else 0.0,
        "total_costs": float(trades["costs_paid"].sum()) if not trades.empty else 0.0,
        "benchmark_buy_and_hold_return": benchmark_return,
        "excess_over_benchmark": (
            total_return - benchmark_return if np.isfinite(benchmark_return) else float("nan")
        ),
    }
