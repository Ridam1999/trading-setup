"""Helpers for retaining separate adjusted and raw close values."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]


def attach_raw_close(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy with raw close populated where an unadjusted source is known."""
    result = frame.copy()
    raw_source = result["source"].eq("nse_bhavcopy_raw")
    result.loc[raw_source & result["close_raw"].isna(), "close_raw"] = result.loc[
        raw_source & result["close_raw"].isna(), "close"
    ]
    return result
