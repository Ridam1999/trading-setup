"""Load and apply corporate actions to raw daily OHLCV bars."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

ACTION_TYPES = frozenset({"split", "bonus", "consolidation", "rights", "dividend"})
REQUIRED_COLUMNS = ("symbol", "ex_date", "action_type", "price_factor")
PRICE_COLUMNS = ("open", "high", "low", "close")


def load_corporate_actions(path: str | Path) -> pd.DataFrame:
    """Read a corporate-action CSV and return one compounded factor per symbol/ex-date."""
    actions = pd.read_csv(path)
    missing = set(REQUIRED_COLUMNS).difference(actions.columns)
    if missing:
        raise ValueError(f"Corporate actions are missing columns: {', '.join(sorted(missing))}")
    if actions.empty:
        raise ValueError(f"Corporate-action file is empty: {path}")

    actions["symbol"] = actions["symbol"].astype("string").str.strip().str.upper()
    actions["ex_date"] = pd.to_datetime(actions["ex_date"], errors="coerce").dt.date
    actions["action_type"] = actions["action_type"].astype("string").str.strip().str.lower()
    actions["price_factor"] = pd.to_numeric(actions["price_factor"], errors="coerce")

    valid_factor = actions["price_factor"].map(
        lambda value: isinstance(value, int | float)
        and math.isfinite(float(value))
        and float(value) > 0
    )
    invalid = (
        actions["symbol"].isna()
        | actions["ex_date"].isna()
        | ~actions["action_type"].isin(ACTION_TYPES)
        | ~valid_factor
    )
    if invalid.any():
        raise ValueError("Corporate actions contain an invalid symbol, date, type, or factor")
    if actions.duplicated(["symbol", "ex_date", "action_type"]).any():
        raise ValueError("Corporate actions contain duplicate symbol/ex-date/type rows")

    grouped = (
        actions.groupby(["symbol", "ex_date"], as_index=False)["price_factor"]
        .prod()
        .sort_values(["symbol", "ex_date"], ignore_index=True)
    )
    return grouped


def _price_factors(bars: pd.DataFrame, actions: pd.DataFrame) -> pd.Series:
    """Return the compounded price factor for each bar from its symbol and date."""
    factors = np.ones(len(bars), dtype=float)
    if actions.empty:
        return pd.Series(factors, index=bars.index)

    ordered = bars.reset_index(drop=True)
    for symbol, group in ordered.groupby("symbol", sort=False):
        symbol_actions = actions.loc[actions["symbol"].eq(symbol)].sort_values("ex_date")
        if symbol_actions.empty:
            continue
        ex_dates = pd.to_datetime(symbol_actions["ex_date"])
        suffix = symbol_actions["price_factor"].to_numpy(dtype=float)[::-1].cumprod()[::-1]
        right = pd.DataFrame({"ex_date": ex_dates.to_numpy(), "suffix_factor": suffix})
        left = pd.DataFrame(
            {
                "_position": group.index.to_numpy(),
                "date": pd.to_datetime(group["date"]).to_numpy(),
            }
        ).sort_values("date")
        matched = pd.merge_asof(
            left,
            right,
            left_on="date",
            right_on="ex_date",
            direction="forward",
            allow_exact_matches=False,
        )
        factors[matched["_position"].to_numpy(dtype=int)] = (
            matched["suffix_factor"].fillna(1.0).to_numpy(dtype=float)
        )
    return pd.Series(factors, index=bars.index)


def apply_corporate_actions(
    bars: pd.DataFrame, actions: pd.DataFrame, source: str = "pit_adjusted"
) -> pd.DataFrame:
    """Adjust historical prices and volumes without changing the raw close."""
    required = {"symbol", "date", *PRICE_COLUMNS, "volume"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"Bars are missing columns: {', '.join(sorted(missing))}")
    frame = bars.copy().reset_index(drop=True)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    if frame["date"].isna().any():
        raise ValueError("Bars contain an invalid date")

    raw_close = frame["close"].copy()
    factors = _price_factors(frame, actions)
    for column in PRICE_COLUMNS:
        frame[column] = frame[column] * factors
    frame["volume"] = frame["volume"] / factors
    if "close_raw" not in frame.columns:
        frame["close_raw"] = raw_close
    else:
        frame["close_raw"] = frame["close_raw"].fillna(raw_close)
    frame["source"] = source

    invalid_range = (
        (frame["high"] < frame["low"])
        | (frame["close"] < frame["low"])
        | (frame["close"] > frame["high"])
        | (frame["open"] < frame["low"])
        | (frame["open"] > frame["high"])
    )
    if invalid_range.any():
        raise ValueError("Corporate-action adjustment produced invalid OHLC bars")
    return frame.sort_values(["symbol", "date"], ignore_index=True)


def validate_adjustments(
    bars: pd.DataFrame, actions: pd.DataFrame, maximum_return_pct: float
) -> pd.DataFrame:
    """Return large returns on the first session at or after each action's ex-date."""
    if maximum_return_pct <= 0:
        raise ValueError("Maximum return threshold must be positive")
    ordered = bars.sort_values(["symbol", "date"]).reset_index(drop=True)
    returns = ordered.groupby("symbol")["close"].pct_change().abs().mul(100)
    findings: list[dict[str, object]] = []
    for action in actions.itertuples(index=False):
        candidates = ordered.index[
            ordered["symbol"].eq(action.symbol) & ordered["date"].ge(action.ex_date)
        ]
        if len(candidates) == 0:
            continue
        position = int(candidates[0])
        change = returns.loc[position]
        if pd.notna(change) and float(change) > maximum_return_pct:
            findings.append(
                {
                    "symbol": str(action.symbol),
                    "date": ordered.loc[position, "date"],
                    "action_type": "corporate_action",
                    "detail": f"Adjusted return {float(change):.2f}% exceeds configured threshold",
                }
            )
    return pd.DataFrame(findings, columns=["symbol", "date", "action_type", "detail"])
