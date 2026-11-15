"""Standalone HTML charts and summary report for portfolio simulations."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import plotly.graph_objects as go  # type: ignore[import-untyped]
from plotly.subplots import make_subplots  # type: ignore[import-untyped]

DISCLAIMER = "Research output, not advice; no guarantee of profit."


def write_backtest_report(
    equity: pd.DataFrame,
    trades: pd.DataFrame,
    metrics: dict[str, Any],
    cost_stress: dict[str, dict[str, Any]],
    monte_carlo: dict[str, float | int],
    output_path: Path,
    survivorship_biased: bool,
) -> Path:
    """Write a self-contained HTML report with Plotly equity and drawdown charts."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=("Portfolio and benchmark growth", "Portfolio drawdown"),
    )
    if not equity.empty:
        first_equity = float(equity["equity"].iloc[0])
        figure.add_trace(
            go.Scatter(x=equity["date"], y=equity["equity"] / first_equity, name="Portfolio"),
            row=1,
            col=1,
        )
        benchmark = pd.to_numeric(equity["benchmark_close"], errors="coerce")
        valid_benchmark = benchmark.notna()
        if valid_benchmark.any():
            base = float(benchmark.loc[valid_benchmark].iloc[0])
            figure.add_trace(
                go.Scatter(
                    x=equity.loc[valid_benchmark, "date"],
                    y=benchmark.loc[valid_benchmark] / base,
                    name="Benchmark",
                ),
                row=1,
                col=1,
            )
        drawdown = equity["equity"] / equity["equity"].cummax() - 1
        figure.add_trace(
            go.Scatter(x=equity["date"], y=drawdown, name="Drawdown", fill="tozeroy"),
            row=2,
            col=1,
        )
    figure.update_layout(template="plotly_white", height=760, title="Portfolio backtest")
    chart = figure.to_html(full_html=False, include_plotlyjs=True)
    metrics_table = "".join(
        f"<tr><th>{html.escape(str(key.replace('_', ' ').title()))}</th>"
        f"<td>{html.escape(str(value))}</td></tr>"
        for key, value in metrics.items()
    )
    stress_rows = "".join(
        "<tr>"
        f"<td>{html.escape(name)}</td>"
        f"<td>{summary.get('total_return', float('nan')):.4%}</td>"
        f"<td>{summary.get('maximum_drawdown', float('nan')):.4%}</td>"
        f"<td>{summary.get('n_trades', 0)}</td>"
        "</tr>"
        for name, summary in cost_stress.items()
    )
    bias_text = (
        "static universe; survivorship bias likely"
        if survivorship_biased
        else "point-in-time universe"
    )
    html_document = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>Portfolio backtest</title></head><body>"
        "<h1>Portfolio backtest report</h1>"
        f"<p>{DISCLAIMER}</p>"
        f"<p>Survivorship-bias status: {html.escape(bias_text)}</p>"
        f"<p>Closed trades: {len(trades)}</p>"
        f"{chart}<h2>Portfolio metrics</h2><table>{metrics_table}</table>"
        "<h2>Cost stress</h2><table><thead><tr><th>Cost multiplier</th>"
        "<th>Total return</th><th>Maximum drawdown</th><th>Trades</th></tr></thead>"
        f"<tbody>{stress_rows}</tbody></table>"
        "<h2>Trade-sequence Monte Carlo drawdown</h2>"
        f"<pre>{html.escape(json.dumps(monte_carlo, indent=2))}</pre>"
        "</body></html>"
    )
    output_path.write_text(html_document, encoding="utf-8")
    return output_path
