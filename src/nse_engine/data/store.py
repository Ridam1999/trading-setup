"""Persist canonical market tables in year-partitioned Parquet and DuckDB."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd  # type: ignore[import-untyped]

from nse_engine.data.sources.base import OHLCV_COLUMNS


class MarketDataStore:
    """Manage durable Parquet partitions and a local DuckDB catalog."""

    def __init__(self, adjusted_dir: Path, duckdb_path: Path) -> None:
        """Initialize store locations without creating files until data is written."""
        self.adjusted_dir = adjusted_dir
        self.duckdb_path = duckdb_path

    def read_ohlcv(self) -> pd.DataFrame:
        """Read all committed daily bars; return an empty canonical frame if absent."""
        paths = sorted(self.adjusted_dir.glob("year=*/ohlcv.parquet"))
        if not paths:
            return pd.DataFrame(columns=list(OHLCV_COLUMNS))
        return pd.concat((pd.read_parquet(path) for path in paths), ignore_index=True)

    def write_ohlcv(self, incoming: pd.DataFrame) -> pd.DataFrame:
        """Merge non-empty bars by symbol/date and update Parquet and DuckDB."""
        if incoming.empty:
            raise ValueError("Refusing to overwrite market data with an empty import")
        existing = self.read_ohlcv()
        merged = pd.concat([existing, incoming], ignore_index=True)
        is_raw = merged["source"].eq("nse_bhavcopy_raw")
        has_raw_close = merged["close_raw"].notna()
        raw_candidates = merged.loc[is_raw | has_raw_close].copy()
        raw_candidates["close_raw_latest"] = raw_candidates["close"].where(
            raw_candidates["source"].eq("nse_bhavcopy_raw"), raw_candidates["close_raw"]
        )
        raw_closes = raw_candidates.sort_values("ingested_at").drop_duplicates(
            ["symbol", "date"], keep="last"
        )[["symbol", "date", "close_raw_latest"]]
        merged["source_priority"] = merged["source"].eq("eod2_adjusted").astype(int)
        merged = merged.sort_values(["source_priority", "ingested_at"])
        merged = merged.drop_duplicates(["symbol", "date"], keep="last")
        merged = merged.drop(columns="source_priority")
        merged = merged.drop(columns="close_raw").merge(
            raw_closes, on=["symbol", "date"], how="left"
        )
        merged["close_raw"] = merged["close_raw_latest"]
        merged = merged.drop(columns="close_raw_latest")
        merged = merged.sort_values(["symbol", "date"], ignore_index=True)
        for year, frame in merged.groupby(pd.to_datetime(merged["date"]).dt.year, sort=True):
            partition = self.adjusted_dir / f"year={year}"
            partition.mkdir(parents=True, exist_ok=True)
            destination = partition / "ohlcv.parquet"
            temporary = partition / "ohlcv.parquet.tmp"
            frame.to_parquet(temporary, index=False)
            temporary.replace(destination)
        self._refresh_duckdb(merged)
        return merged

    def write_calendar(self, calendar: pd.DataFrame) -> None:
        """Replace the DuckDB calendar table with inferred trading dates."""
        if calendar.empty:
            raise ValueError("Refusing to write an empty trading calendar")
        calendar_to_write = calendar.copy()
        calendar_to_write["year"] = pd.to_datetime(calendar_to_write["date"]).dt.year.astype(str)
        self._write_partitions(calendar_to_write, "calendar", "calendar")
        self.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.duckdb_path)) as connection:
            connection.register("calendar_batch", calendar)
            connection.execute("CREATE OR REPLACE TABLE calendar AS SELECT * FROM calendar_batch")

    def write_universe(self, membership: pd.DataFrame) -> None:
        """Register configured index memberships when a static or PIT list exists."""
        if not membership.empty:
            membership_to_write = membership.copy()
            years = pd.to_datetime(membership_to_write["start_date"], errors="coerce").dt.year
            membership_to_write["year"] = years.astype("Int64").astype("string").fillna("unknown")
            self._write_partitions(membership_to_write, "universe_membership", "membership")
        self.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.duckdb_path)) as connection:
            connection.register("universe_batch", membership)
            connection.execute(
                "CREATE OR REPLACE TABLE universe_membership AS SELECT * FROM universe_batch"
            )

    def _write_partitions(self, frame: pd.DataFrame, table: str, filename: str) -> None:
        """Write a table to atomic year-partitioned Parquet files."""
        for year, partition_frame in frame.groupby("year", sort=True, dropna=False):
            partition = self.adjusted_dir / table / f"year={year}"
            partition.mkdir(parents=True, exist_ok=True)
            destination = partition / f"{filename}.parquet"
            temporary = partition / f"{filename}.parquet.tmp"
            partition_frame.drop(columns="year").to_parquet(temporary, index=False)
            temporary.replace(destination)

    def _refresh_duckdb(self, frame: pd.DataFrame) -> None:
        """Atomically refresh the local query table from merged data."""
        self.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.duckdb_path)) as connection:
            connection.register("ohlcv_batch", frame)
            connection.execute("CREATE OR REPLACE TABLE ohlcv_daily AS SELECT * FROM ohlcv_batch")
