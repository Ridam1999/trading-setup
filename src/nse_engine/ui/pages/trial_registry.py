"""Read-only trial registry page backed by the append-only SQLite registry."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.reporting import charts
from nse_engine.ui import common
from nse_engine.ui.config import load_ui_settings


def registry_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Flatten report-eligible registry rows for display only."""
    records: list[dict[str, Any]] = []
    for row in rows:
        metrics = json.loads(row["metrics_json"])
        excess = metrics.get("excess_over_baseline") or {}
        records.append(
            {
                "trial_id": row["trial_id"],
                "timestamp_utc": row["timestamp_utc"],
                "setup": row["setup_name"],
                "params_hash": row["params_hash"],
                "universe": row["universe"],
                "split": row["split"],
                "horizon": int(row["horizon"]),
                "date_clustered_p": excess.get("date_clustered_p"),
            }
        )
    return pd.DataFrame.from_records(records)


def render() -> None:
    """Render trial rows and counts without modifying the registry."""
    common.render_header("Trial registry")
    settings = load_ui_settings()
    provisional = common.st.checkbox("Provisional kill-stage rows only", value=True)
    frame = registry_frame(common.load_trial_rows(provisional))
    common.st.caption("Append-only registry; no trial is rerun, updated, or deleted here.")

    if frame.empty:
        common.st.info("No eligible trials found.")
        common.render_footer()
        return

    common.st.dataframe(frame.head(settings.table_rows), hide_index=True)
    counts = (
        frame.groupby("setup", as_index=False)
        .size()
        .rename(columns={"size": "count"})
        .rename(columns={"setup": "group"})
    )
    common.st.plotly_chart(charts.trial_counts_chart(counts))
    common.render_footer()
