"""Read-only data-health page backed by the existing quality report CSV."""

from __future__ import annotations

from nse_engine.reporting import charts
from nse_engine.ui import common
from nse_engine.ui.config import load_ui_settings


def render() -> None:
    """Render data-quality findings as a table and grouped chart."""
    common.render_header("Data health")
    settings = load_ui_settings()
    findings = common.load_data_health()
    if findings.empty:
        common.st.info("No data-health CSV found. Run `nse-engine data check` first.")
    else:
        common.st.caption("Findings are read from the last saved check; nothing is recomputed.")
        common.st.dataframe(findings.head(settings.table_rows), hide_index=True)
        summary = (
            findings.groupby(["check", "severity"], as_index=False)
            .size()
            .rename(columns={"size": "count"})
        )
        common.st.plotly_chart(charts.severity_summary_chart(summary))
    common.render_footer()
