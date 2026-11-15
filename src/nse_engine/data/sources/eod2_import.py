"""Import adjusted daily bars from EOD2 CSV files."""

from __future__ import annotations

from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.data.sources.base import normalize_ohlcv


class EOD2Source:
    """Read an EOD2 CSV into the canonical adjusted OHLCV schema."""

    def load(self, path: str | Path) -> pd.DataFrame:
        """Read a local EOD2 CSV file, failing if it has no data."""
        frame = pd.read_csv(path, sep=None, engine="python")
        return normalize_ohlcv(frame, source="eod2_adjusted")
