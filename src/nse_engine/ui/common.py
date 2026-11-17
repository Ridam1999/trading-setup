"""Shared Streamlit chrome: title, bias banner, footer, and safe store loaders."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import streamlit as st

from nse_engine.config import load_config, load_data_bias_status, load_data_config
from nse_engine.data.store import MarketDataStore
from nse_engine.paper.config import load_paper_settings
from nse_engine.study.config import load_study_settings
from nse_engine.study.trials import TrialRegistry

DISCLAIMER = "Research output, not advice; no guarantee of profit."
EVENT_FILE_PATTERN = re.compile(r"^(.+?)_(train|validation)_(\d+)_[0-9a-f]+\.parquet$")

__all__ = [
    "DISCLAIMER",
    "EVENT_FILE_PATTERN",
    "st",
    "load_data_health",
    "load_ohlcv",
    "load_events",
    "load_backtest_folders",
    "load_ledger_rows",
    "load_trial_rows",
    "render_footer",
    "render_header",
]


def render_header(page: str) -> str:
    """Render the page title, bias banner, and caption; return the bias status."""
    status = load_data_bias_status(load_config())
    st.set_page_config(page_title=f"NSE Engine - {page}", layout="wide")
    st.title(f"NSE Engine - {page}")
    st.warning(f"Data-bias status: {status}. Provisional research only.")
    st.caption("Holdout is locked; no live orders are enabled.")
    return status


def render_footer() -> None:
    """Render the mandatory disclaimer at the bottom of every page."""
    st.markdown("---")
    st.caption(DISCLAIMER)


def load_data_health() -> pd.DataFrame:
    """Read the existing row-level data-health CSV; never recompute it in the UI."""
    path = load_data_config().reports_dir / "data_health.csv"
    return pd.read_csv(path) if path.is_file() else pd.DataFrame()


def load_ohlcv() -> pd.DataFrame:
    """Load the adjusted OHLCV store read-only for the setup explorer."""
    settings = load_data_config()
    return MarketDataStore(settings.adjusted_dir, settings.duckdb_path).read_ohlcv()


def load_events(include_controls: bool = False) -> pd.DataFrame:
    """Load saved event Parquet files, tagging each row with setup and split.

    The invalid look-ahead control is excluded by default so it can never be
    mistaken for research evidence in a chart or table.
    """
    events_dir = load_study_settings().events_dir
    if not events_dir.is_dir():
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    for path in sorted(events_dir.glob("*.parquet")):
        match = EVENT_FILE_PATTERN.match(path.name)
        if match is None:
            continue
        setup, split, horizon = match.groups()
        if setup.startswith("__control") and not include_controls:
            continue
        frame = pd.read_parquet(path)
        frame["setup"] = setup
        frame["split"] = split
        frame["horizon"] = int(horizon)
        frame["source_file"] = path.name
        frames.append(frame)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_backtest_folders() -> list[dict[str, Any]]:
    """List immutable backtest run folders without modifying their contents."""
    reports_dir = load_data_config().reports_dir
    folders = sorted(reports_dir.glob("backtest_*"))
    return [
        {
            "label": folder.name,
            "path": folder,
            "equity": _read_optional(folder / "equity.parquet"),
            "trades": _read_optional(folder / "trades.parquet"),
            "windows": _read_optional(folder / "windows.parquet"),
        }
        for folder in folders
    ]


def _read_optional(path: Path) -> pd.DataFrame:
    """Read a Parquet file if present, returning an empty frame otherwise."""
    return pd.read_parquet(path) if path.is_file() else pd.DataFrame()


def load_ledger_rows() -> dict[str, pd.DataFrame]:
    """Load the paper ledger's five report tables as read-only frames."""
    from nse_engine.paper.ledger import PaperLedger

    settings = load_paper_settings()
    rows = PaperLedger(settings.ledger_path).report_rows()
    return {name: pd.DataFrame(values) for name, values in rows.items()}


def load_trial_rows(provisional: bool) -> list[dict[str, Any]]:
    """Load report-eligible registry rows without touching the approval file."""
    from nse_engine.cli import _eligible_report_rows

    registry = TrialRegistry(load_study_settings().trial_registry_path)
    return _eligible_report_rows(registry, provisional)
