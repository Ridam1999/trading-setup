"""Phase 5 chart builder and standalone HTML report tests."""

from pathlib import Path

import pandas as pd
import pytest

from nse_engine.reporting import charts
from nse_engine.reporting.html_report import DISCLAIMER, write_chart_report


def test_empty_chart_builders_still_render() -> None:
    """Empty inputs produce labelled figures instead of exceptions."""
    empty = pd.DataFrame()
    figures = [
        charts.equity_curve_chart(empty),
        charts.drawdown_chart(empty),
        charts.return_distribution_chart(pd.Series(dtype=float)),
        charts.yearly_returns_chart(empty),
        charts.mae_mfe_scatter(empty),
        charts.parameter_stability_heatmap(empty),
        charts.severity_summary_chart(empty),
        charts.window_metric_chart(empty, "cagr"),
        charts.trial_counts_chart(empty),
        charts.line_chart(empty, "x", "y", "Empty line"),
    ]
    for figure in figures:
        assert "no data" in str(figure.layout.title.text)


def test_equity_chart_normalises_portfolio_and_benchmark() -> None:
    """The equity builder plots both series normalised to their first value."""
    equity = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "equity": [100.0, 110.0],
            "benchmark_close": [200.0, 210.0],
        }
    )
    figure = charts.equity_curve_chart(equity)
    assert [trace.name for trace in figure.data] == ["Portfolio", "Benchmark"]
    assert list(figure.data[0].y) == [1.0, 1.1]
    assert list(figure.data[1].y) == [1.0, 1.05]


def test_candlestick_chart_adds_events_and_overlays() -> None:
    """Saved event markers and overlays are drawn on the price chart."""
    bars = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-01", "2026-01-02"]),
            "open": [10.0, 11.0],
            "high": [12.0, 13.0],
            "low": [9.0, 10.0],
            "close": [11.0, 12.0],
        }
    )
    events = pd.DataFrame({"signal_date": [pd.Timestamp("2026-01-02")], "entry_price": [12.0]})
    figure = charts.candlestick_chart(bars, events, {"SMA": bars["close"]})
    assert [trace.name for trace in figure.data] == ["OHLC", "SMA", "Signals"]


def test_heatmap_carries_overfitting_warning() -> None:
    """Every parameter heatmap visibly warns against exploratory overfitting."""
    matrix = pd.DataFrame({"5": [0.01, 0.02], "10": [0.0, 0.03]}, index=["a", "b"])
    figure = charts.parameter_stability_heatmap(matrix)
    assert any(charts.HEATMAP_WARNING in str(note.text) for note in figure.layout.annotations)


def test_grouped_and_window_charts_use_preaggregated_columns() -> None:
    """Display-only aggregation frames map to the expected chart traces."""
    summary = pd.DataFrame(
        {
            "check": ["zero_volume", "zero_volume", "stale"],
            "severity": ["warning", "critical", "warning"],
            "count": [2, 1, 3],
        }
    )
    severity = charts.severity_summary_chart(summary)
    assert len(severity.data) == 2

    windows = pd.DataFrame({"window": ["w1", "w2"], "cagr": [0.1, -0.2]})
    assert len(charts.window_metric_chart(windows, "cagr").data) == 1
    counts = pd.DataFrame({"group": ["a", "b"], "count": [4, 5]})
    assert len(charts.trial_counts_chart(counts).data) == 1


def test_html_report_has_disclaimer_and_bias_banner(tmp_path: Path) -> None:
    """Standalone chart reports carry the disclaimer and survivorship status."""
    path = tmp_path / "charts" / "report.html"
    figure = charts.line_chart(pd.DataFrame({"x": [1], "y": [2]}), "x", "y", "Demo")
    result = write_chart_report([("Demo", figure)], path, "SURVIVOR_BIASED")
    text = result.read_text(encoding="utf-8")
    assert text.count(DISCLAIMER) == 2
    assert "Data-bias status: survivor biased" in text
    assert "Demo" in text


def test_html_report_rejects_empty_figure_list(tmp_path: Path) -> None:
    """An empty report cannot be written silently."""
    with pytest.raises(ValueError, match="at least one figure"):
        write_chart_report([], tmp_path / "empty.html", "SURVIVOR_BIASED")
