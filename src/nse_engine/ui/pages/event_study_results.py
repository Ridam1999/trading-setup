"""Read-only event-study page backed by saved event Parquet files and trials."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.reporting import charts
from nse_engine.ui import common
from nse_engine.ui.config import load_ui_settings


def trial_summary_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Flatten saved trial rows for display without recomputing any statistic."""
    records: list[dict[str, Any]] = []
    for row in rows:
        metrics = json.loads(row["metrics_json"])
        net = metrics.get("net_return") or {}
        excess = metrics.get("excess_over_baseline") or {}
        records.append(
            {
                "setup": row["setup_name"],
                "params_hash": row["params_hash"],
                "split": row["split"],
                "horizon": int(row["horizon"]),
                "events": net.get("n_events"),
                "expectancy": net.get("expectancy"),
                "hit_rate": net.get("hit_rate"),
                "date_clustered_p": excess.get("date_clustered_p"),
                "yearly": metrics.get("yearly") or [],
            }
        )
    return pd.DataFrame.from_records(records)


def render() -> None:
    """Render registry metrics, yearly expectancy, and event outcome charts."""
    common.render_header("Event-study results")
    settings = load_ui_settings()
    provisional = common.st.checkbox("Provisional kill-stage rows only", value=True)
    rows = common.load_trial_rows(provisional)
    frame = trial_summary_frame(rows)
    common.st.caption("Rows are read from the append-only trial registry; nothing is rerun here.")

    if frame.empty:
        common.st.info("No eligible trials found. Run a study before using this page.")
        common.render_footer()
        return

    display = frame.drop(columns=["yearly"])
    common.st.dataframe(display.head(settings.table_rows), hide_index=True)

    setups = sorted(display["setup"].dropna().unique().tolist())
    setup = common.st.selectbox("Setup", setups)
    horizons = sorted(display.loc[display["setup"] == setup, "horizon"].unique().tolist())
    horizon = common.st.selectbox("Horizon", horizons)
    selected = frame.loc[(frame["setup"] == setup) & (frame["horizon"] == horizon)]

    yearly_parts = [pd.DataFrame(item) for item in selected["yearly"] if item]
    yearly = pd.concat(yearly_parts, ignore_index=True) if yearly_parts else pd.DataFrame()
    common.st.plotly_chart(charts.yearly_returns_chart(yearly))

    events = common.load_events()
    if not events.empty and {"setup", "horizon"}.issubset(events.columns):
        subset = events.loc[(events["setup"] == setup) & (events["horizon"] == horizon)].head(
            settings.event_sample_rows
        )
    else:
        subset = pd.DataFrame()
    common.st.plotly_chart(charts.return_distribution_chart(subset.get("net_return", pd.Series())))
    common.st.plotly_chart(charts.mae_mfe_scatter(subset))
    common.render_footer()
