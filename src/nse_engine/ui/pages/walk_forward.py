"""Read-only walk-forward page backed by saved window Parquet files."""

from __future__ import annotations

from nse_engine.reporting import charts
from nse_engine.ui import common
from nse_engine.ui.config import load_ui_settings


def render() -> None:
    """Render saved walk-forward windows and per-window CAGR."""
    common.render_header("Walk-forward")
    settings = load_ui_settings()
    folders = [folder for folder in common.load_backtest_folders() if not folder["windows"].empty]
    if not folders:
        common.st.info(
            "No walk-forward windows found. Run `nse-engine backtest walk-forward` first."
        )
        common.render_footer()
        return

    label = common.st.selectbox("Run", [folder["label"] for folder in folders])
    run = next(folder for folder in folders if folder["label"] == label)
    windows = run["windows"]
    common.st.dataframe(windows.head(settings.table_rows), hide_index=True)
    common.st.plotly_chart(charts.window_metric_chart(windows, "cagr"))
    common.render_footer()
