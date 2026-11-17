"""Read-only portfolio backtest page backed by saved run folders."""

from __future__ import annotations

from nse_engine.reporting import charts
from nse_engine.ui import common
from nse_engine.ui.config import load_ui_settings


def render() -> None:
    """Render equity, drawdown, and closed trades from one immutable run."""
    common.render_header("Backtest")
    settings = load_ui_settings()
    folders = common.load_backtest_folders()
    if not folders:
        common.st.info("No backtest run folders found. Run `nse-engine backtest run` first.")
        common.render_footer()
        return

    label = common.st.selectbox("Run", [folder["label"] for folder in folders])
    run = next(folder for folder in folders if folder["label"] == label)
    equity = run["equity"]
    trades = run["trades"]

    common.st.plotly_chart(charts.equity_curve_chart(equity))
    common.st.plotly_chart(charts.drawdown_chart(equity))
    if trades.empty:
        common.st.info("This run has no closed trades.")
    else:
        common.st.dataframe(trades.head(settings.table_rows), hide_index=True)

    report_path = run["path"] / "report.html"
    if report_path.is_file():
        common.st.caption(f"Full HTML report: {report_path}")
    common.render_footer()
