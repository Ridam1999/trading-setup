"""Read-only paper ledger page backed by the durable SQLite ledger."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.reporting import charts
from nse_engine.ui import common
from nse_engine.ui.config import load_ui_settings


def render() -> None:
    """Render ledger tables and the saved equity series."""
    common.render_header("Paper ledger")
    settings = load_ui_settings()
    rows = common.load_ledger_rows()
    table = common.st.selectbox("Ledger table", sorted(rows))
    common.st.dataframe(rows[table].head(settings.table_rows), hide_index=True)

    ledger = rows.get("ledger", pd.DataFrame())
    if not ledger.empty and {"entry_date", "equity"}.issubset(ledger.columns):
        common.st.plotly_chart(
            charts.line_chart(ledger, "entry_date", "equity", "Paper ledger equity")
        )
    else:
        common.st.info("No ledger equity rows recorded yet.")
    common.render_footer()
