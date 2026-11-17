"""Pure Plotly chart builders used by both HTML reports and the Streamlit UI.

Every builder accepts a DataFrame and returns a figure; none reads a store,
writes a file, or contains research logic. Empty inputs produce a labelled
empty figure so a page still renders when an artifact has no rows.
"""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]
import plotly.graph_objects as go  # type: ignore[import-untyped]

HEATMAP_WARNING = "Heatmaps invite overfitting: treat this as exploratory, not evidence."


def _empty(title: str) -> go.Figure:
    """Return an empty figure with a title and a readable message."""
    figure = go.Figure()
    figure.update_layout(template="plotly_white", title=f"{title} (no data)", height=420)
    figure.add_annotation(text="No data available", showarrow=False)
    return figure


def candlestick_chart(
    bars: pd.DataFrame,
    events: pd.DataFrame | None = None,
    overlays: dict[str, pd.Series] | None = None,
) -> go.Figure:
    """Draw OHLC candles with optional indicator overlays and event markers."""
    required = {"date", "open", "high", "low", "close"}
    if bars.empty or not required.issubset(bars.columns):
        return _empty("Price and signals")
    figure = go.Figure(
        data=[
            go.Candlestick(
                x=bars["date"],
                open=bars["open"],
                high=bars["high"],
                low=bars["low"],
                close=bars["close"],
                name="OHLC",
            )
        ]
    )
    for name, series in (overlays or {}).items():
        figure.add_trace(
            go.Scatter(x=bars["date"], y=series.reindex(bars.index), mode="lines", name=name)
        )
    if events is not None and not events.empty and "signal_date" in events.columns:
        figure.add_trace(
            go.Scatter(
                x=events["signal_date"],
                y=events.get("entry_price", pd.Series(dtype=float)),
                mode="markers",
                marker={"symbol": "triangle-up", "size": 9},
                name="Signals",
            )
        )
    figure.update_layout(template="plotly_white", title="Price and signals", height=560)
    return figure


def equity_curve_chart(equity: pd.DataFrame) -> go.Figure:
    """Draw portfolio equity against the benchmark, both normalised to one."""
    if equity.empty or "date" not in equity.columns or "equity" not in equity.columns:
        return _empty("Equity curve")
    first = float(equity["equity"].iloc[0])
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=equity["date"], y=equity["equity"] / first, name="Portfolio"))
    if "benchmark_close" in equity.columns:
        benchmark = equity["benchmark_close"].dropna()
        if not benchmark.empty:
            figure.add_trace(
                go.Scatter(
                    x=equity.loc[benchmark.index, "date"],
                    y=benchmark / float(benchmark.iloc[0]),
                    name="Benchmark",
                )
            )
    figure.update_layout(template="plotly_white", title="Equity curve", height=440)
    return figure


def drawdown_chart(equity: pd.DataFrame) -> go.Figure:
    """Draw the underwater drawdown series from an equity curve."""
    if equity.empty or "date" not in equity.columns or "equity" not in equity.columns:
        return _empty("Drawdown")
    drawdown = equity["equity"] / equity["equity"].cummax() - 1.0
    figure = go.Figure(go.Scatter(x=equity["date"], y=drawdown, fill="tozeroy", name="Drawdown"))
    figure.update_layout(template="plotly_white", title="Drawdown", height=360)
    return figure


def return_distribution_chart(returns: pd.Series) -> go.Figure:
    """Draw a histogram of per-trade or per-event net returns."""
    clean = returns.dropna()
    if clean.empty:
        return _empty("Return distribution")
    figure = go.Figure(go.Histogram(x=clean, nbinsx=40, name="Returns"))
    figure.update_layout(template="plotly_white", title="Return distribution", height=400)
    return figure


def yearly_returns_chart(yearly: pd.DataFrame) -> go.Figure:
    """Draw a per-year expectancy bar chart from a study yearly table."""
    if yearly.empty or "year" not in yearly.columns or "expectancy" not in yearly.columns:
        return _empty("Yearly expectancy")
    figure = go.Figure(
        go.Bar(x=yearly["year"].astype(str), y=yearly["expectancy"], name="Expectancy")
    )
    figure.update_layout(template="plotly_white", title="Yearly expectancy", height=400)
    return figure


def mae_mfe_scatter(events: pd.DataFrame) -> go.Figure:
    """Draw MAE versus MFE for event outcomes."""
    if events.empty or not {"mae", "mfe"}.issubset(events.columns):
        return _empty("MAE vs MFE")
    figure = go.Figure(
        go.Scatter(
            x=events["mae"],
            y=events["mfe"],
            mode="markers",
            name="Events",
        )
    )
    figure.update_layout(
        template="plotly_white",
        title="MAE vs MFE",
        xaxis_title="MAE",
        yaxis_title="MFE",
        height=440,
    )
    return figure


def parameter_stability_heatmap(matrix: pd.DataFrame) -> go.Figure:
    """Draw a parameter stability heatmap with an explicit overfitting warning."""
    if matrix.empty:
        figure = _empty("Parameter stability")
    else:
        figure = go.Figure(
            go.Heatmap(
                z=matrix.to_numpy(dtype=float),
                x=[str(column) for column in matrix.columns],
                y=[str(index) for index in matrix.index],
                colorbar={"title": "Expectancy"},
            )
        )
        figure.update_layout(template="plotly_white", title="Parameter stability", height=480)
    figure.add_annotation(
        text=HEATMAP_WARNING,
        showarrow=False,
        xref="paper",
        yref="paper",
        x=0.5,
        y=-0.12,
    )
    return figure


def monte_carlo_fan(samples: pd.DataFrame) -> go.Figure:
    """Draw Monte Carlo equity paths as a light fan of lines."""
    if samples.empty:
        return _empty("Monte Carlo drawdown paths")
    figure = go.Figure()
    for column in samples.columns:
        figure.add_trace(
            go.Scatter(
                y=samples[column],
                mode="lines",
                line={"width": 1},
                showlegend=False,
                name=str(column),
            )
        )
    figure.update_layout(template="plotly_white", title="Monte Carlo paths", height=440)
    return figure


def line_chart(frame: pd.DataFrame, x: str, y: str, title: str) -> go.Figure:
    """Draw one simple line series from two existing DataFrame columns."""
    if frame.empty or not {x, y}.issubset(frame.columns):
        return _empty(title)
    figure = go.Figure(go.Scatter(x=frame[x], y=frame[y], mode="lines", name=y))
    figure.update_layout(template="plotly_white", title=title, height=400)
    return figure


def severity_summary_chart(summary: pd.DataFrame) -> go.Figure:
    """Draw grouped data-health finding counts by check and severity."""
    required = {"severity", "check", "count"}
    if summary.empty or not required.issubset(summary.columns):
        return _empty("Data health findings")
    figure = go.Figure()
    for severity, group in summary.groupby("severity", sort=True):
        figure.add_trace(
            go.Bar(
                x=group["check"].astype(str),
                y=group["count"],
                name=str(severity),
            )
        )
    figure.update_layout(
        template="plotly_white", title="Data health findings", barmode="group", height=460
    )
    return figure


def window_metric_chart(windows: pd.DataFrame, metric: str) -> go.Figure:
    """Draw a walk-forward metric by test window."""
    if windows.empty or not {"window", metric}.issubset(windows.columns):
        return _empty(f"Walk-forward {metric}")
    figure = go.Figure(go.Bar(x=windows["window"].astype(str), y=windows[metric], name=metric))
    figure.update_layout(template="plotly_white", title=f"Walk-forward {metric}", height=420)
    return figure


def trial_counts_chart(counts: pd.DataFrame) -> go.Figure:
    """Draw registry row counts by a pre-aggregated group label."""
    if counts.empty or not {"group", "count"}.issubset(counts.columns):
        return _empty("Trial counts")
    figure = go.Figure(go.Bar(x=counts["group"].astype(str), y=counts["count"], name="Trials"))
    figure.update_layout(template="plotly_white", title="Trial counts", height=420)
    return figure
