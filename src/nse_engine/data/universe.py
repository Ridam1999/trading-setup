"""Load static or point-in-time universe memberships from YAML."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import duckdb
import pandas as pd  # type: ignore[import-untyped]
import yaml

from nse_engine.config import config_directory


def load_universe(path: Path) -> pd.DataFrame:
    """Load configured symbols and annotate static membership survivorship bias."""
    with path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    if not isinstance(config, dict):
        raise ValueError(f"Expected a mapping in {path}")
    symbols = config.get("symbols", [])
    if not isinstance(symbols, list):
        raise ValueError("Universe symbols must be a list")
    return pd.DataFrame(
        {
            "symbol": [str(symbol).strip().upper() for symbol in symbols],
            "index_name": config.get("name", "configured_universe"),
            "start_date": None,
            "end_date": None,
            "membership_type": "static_biased",
        }
    )


def point_in_time_liquidity_membership(
    frame: pd.DataFrame,
    top_n: int,
    trailing_days: int,
    minimum_price: float,
    minimum_history: int,
    excluded_symbol_pattern: str | None = None,
) -> pd.Series:
    """Select each date's top traded-value symbols using only available history."""
    if min(top_n, trailing_days, minimum_history) < 1 or minimum_price < 0:
        raise ValueError("Liquidity-universe thresholds must be positive")
    ordered = frame.sort_values(["symbol", "date"]).copy()
    ordered["_row_id"] = range(len(ordered))
    connection = duckdb.connect()
    try:
        connection.register("bars", ordered)
        selected_rows = connection.execute(
            """
            WITH scored AS (
                SELECT _row_id, symbol, date, close,
                    median(close * volume) OVER (
                        PARTITION BY symbol ORDER BY date
                        ROWS BETWEEN ? PRECEDING AND CURRENT ROW
                    ) AS traded_value_median,
                    row_number() OVER (PARTITION BY symbol ORDER BY date) AS history_rows
                FROM bars
            ), eligible AS (
                SELECT *, row_number() OVER (
                    PARTITION BY date ORDER BY traded_value_median DESC, symbol
                ) AS liquidity_rank
                FROM scored
                WHERE close >= ? AND history_rows >= ? AND traded_value_median IS NOT NULL
                    AND (? IS NULL OR NOT regexp_matches(symbol, ?))
            )
            SELECT _row_id FROM eligible WHERE liquidity_rank <= ?
            """,
            [
                trailing_days - 1,
                minimum_price,
                minimum_history,
                excluded_symbol_pattern,
                excluded_symbol_pattern,
                top_n,
            ],
        ).fetchall()
    finally:
        connection.close()
    selected = pd.Series(False, index=range(len(ordered)))
    if selected_rows:
        selected.loc[[int(row[0]) for row in selected_rows]] = True
    membership = pd.Series(False, index=frame.index)
    membership.loc[ordered.index] = selected.to_numpy()
    return membership


class LiquidityUniverseSettings(TypedDict):
    """Validated point-in-time liquidity thresholds."""

    top_n: int
    trailing_days: int
    minimum_price: float
    minimum_history: int
    excluded_symbol_pattern: str | None


def liquidity_universe_settings(path: Path | None = None) -> LiquidityUniverseSettings:
    """Read and validate top-N liquidity selection thresholds from YAML."""
    config_path = path or config_directory() / "universe.yaml"
    with config_path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    settings = config.get("point_in_time_liquidity", {})
    if not isinstance(settings, dict):
        raise ValueError("point_in_time_liquidity must be a YAML mapping")
    top_n = settings.get("top_n", 200)
    trailing_days = settings.get("trailing_days", 60)
    minimum_price = settings.get("minimum_price", 20.0)
    minimum_history = settings.get("minimum_history", 60)
    excluded = settings.get("excluded_symbol_pattern", r"\s")
    values = (top_n, trailing_days, minimum_price, minimum_history)
    if any(isinstance(value, bool) or not isinstance(value, int | float) for value in values):
        raise ValueError("Liquidity universe thresholds must be numeric")
    if min(top_n, trailing_days, minimum_history) < 1 or minimum_price < 0:
        raise ValueError("Liquidity universe counts must be positive and price non-negative")
    if excluded is not None and not isinstance(excluded, str):
        raise ValueError("excluded_symbol_pattern must be a string or null")
    return {
        "top_n": int(top_n),
        "trailing_days": int(trailing_days),
        "minimum_price": float(minimum_price),
        "minimum_history": int(minimum_history),
        "excluded_symbol_pattern": excluded,
    }
