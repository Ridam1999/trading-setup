"""Import adjusted daily bars from EOD2 CSV files."""

from __future__ import annotations

from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.data.sources.base import normalize_ohlcv


class EOD2Source:
    """Read an EOD2 CSV into the canonical adjusted OHLCV schema."""

    def load(self, path: str | Path) -> pd.DataFrame:
        """Read one EOD2 CSV or a directory of per-symbol CSVs."""
        source_path = Path(path)
        if source_path.is_dir():
            files = sorted(source_path.glob("*.csv"))
            if not files:
                raise ValueError(f"No EOD2 CSV files found in {source_path}")
            frames = [self._load_file(file_path) for file_path in files]
            combined = pd.concat(frames, ignore_index=True)
            if combined.duplicated(["symbol", "date"]).any():
                raise ValueError("EOD2 directory contains duplicate symbol/date rows")
            return combined.sort_values(["symbol", "date"], ignore_index=True)
        if not source_path.is_file():
            raise FileNotFoundError(f"EOD2 input does not exist: {source_path}")
        return self._load_file(source_path)

    @staticmethod
    def _load_file(path: Path) -> pd.DataFrame:
        """Read one file and use its filename when no ticker column is present."""
        frame = pd.read_csv(path, sep=None, engine="python")
        has_symbol = any(
            str(column).strip().lower().replace(" ", "")
            in {"symbol", "symbol_name", "ticker", "tckrsymb", "sc_name", "tradingsymbol"}
            for column in frame.columns
        )
        if not has_symbol:
            frame["symbol"] = path.stem
        return normalize_ohlcv(frame, source="eod2_adjusted")
