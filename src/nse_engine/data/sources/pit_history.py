"""Import raw point-in-time daily bars, including delisted symbols."""

from __future__ import annotations

from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.data.sources.base import normalize_ohlcv


class PitHistorySource:
    """Read a local PIT CSV or Parquet file into the canonical raw schema."""

    def load(self, path: str | Path, source: str = "pit_raw") -> pd.DataFrame:
        """Normalize raw OHLCV rows and retain their unadjusted close."""
        input_path = Path(path)
        if not input_path.is_file():
            raise FileNotFoundError(f"PIT history input does not exist: {input_path}")
        if input_path.suffix.lower() in {".parquet", ".pq"}:
            frame = pd.read_parquet(input_path)
        else:
            frame = pd.read_csv(input_path, sep=None, engine="python")
        normalized = normalize_ohlcv(frame, source=source)
        normalized["close_raw"] = normalized["close_raw"].combine_first(normalized["close"])
        return normalized
