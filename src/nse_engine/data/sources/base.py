"""Shared market-data source contract and canonical OHLCV schema."""

from __future__ import annotations

from typing import Protocol

import pandas as pd  # type: ignore[import-untyped]

OHLCV_COLUMNS: tuple[str, ...] = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_raw",
    "delivery_qty",
    "delivery_pct",
    "source",
    "ingested_at",
)


class DataSource(Protocol):
    """A source that turns tabular market data into the canonical schema."""

    def load(self, path: str) -> pd.DataFrame:
        """Read and normalize one source file."""


def normalize_ohlcv(frame: pd.DataFrame, source: str) -> pd.DataFrame:
    """Normalize common NSE/EOD2 column names and enforce canonical values."""
    if frame.empty:
        raise ValueError("Refusing to import an empty market-data file")
    aliases = {
        "symbol": ("symbol", "symbol_name", "ticker", "tckrsymb", "sc_name", "tradingsymbol"),
        "date": ("date", "trad_dt", "traddt", "date1", "timestamp", "tradedate", "datetime"),
        "open": ("open", "open_price", "opnpric", "adj_open", "adjusted_open"),
        "high": ("high", "high_price", "hghpric", "adj_high", "adjusted_high"),
        "low": ("low", "low_price", "lwpric", "adj_low", "adjusted_low"),
        "close": (
            "close",
            "close_price",
            "clspric",
            "last",
            "adj_close",
            "adjusted_close",
            "close_adjusted",
        ),
        "volume": ("volume", "tottrdqty", "totaltradedquantity", "ttltradgvol"),
        "delivery_qty": ("delivery_qty", "deliverable_qty", "deliv_qty"),
        "delivery_pct": ("delivery_pct", "deliverable_pct", "deliv_per"),
        "close_raw": ("close_raw", "raw_close"),
    }
    lookup = {str(column).strip().lower().replace(" ", ""): column for column in frame.columns}
    rename: dict[object, str] = {}
    for canonical, candidates in aliases.items():
        found = next((lookup[candidate] for candidate in candidates if candidate in lookup), None)
        if found is not None:
            rename[found] = canonical
    normalized = frame.rename(columns=rename).copy()
    required = {"symbol", "date", "open", "high", "low", "close", "volume"}
    missing = required.difference(normalized.columns)
    if missing:
        raise ValueError(f"Missing required OHLCV columns: {', '.join(sorted(missing))}")

    normalized["symbol"] = normalized["symbol"].astype("string").str.strip().str.upper()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce").dt.date
    for column in (
        "open",
        "high",
        "low",
        "close",
        "close_raw",
        "volume",
        "delivery_qty",
        "delivery_pct",
    ):
        if column not in normalized:
            normalized[column] = float("nan")
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    if source == "nse_bhavcopy_raw":
        normalized["close_raw"] = normalized["close_raw"].combine_first(normalized["close"])
    normalized["source"] = source
    normalized["ingested_at"] = pd.Timestamp.now(tz="UTC")
    normalized = normalized.loc[:, list(OHLCV_COLUMNS)]
    if normalized[["symbol", "date", "open", "high", "low", "close", "volume"]].isna().any().any():
        raise ValueError("Input has invalid or missing required values")
    if normalized.duplicated(["symbol", "date"]).any():
        raise ValueError("Input contains duplicate symbol/date rows")
    return normalized.sort_values(["symbol", "date"], ignore_index=True)
