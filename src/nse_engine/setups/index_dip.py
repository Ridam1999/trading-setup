"""Buy a diversified index instrument after a sharp dip in a longer uptrend."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.features.indicators import rate_of_change, sma
from nse_engine.setups.base import SetupConfig, eligible_rows, parameter_number


class IndexDip:
    """Signal a configured index/ETF after a short dip while above its long average."""

    name = "index_dip"

    def __init__(self, config: SetupConfig) -> None:
        """Store the validated parameter set."""
        self.config = config

    def required_features(self) -> list[str]:
        """List input OHLCV columns used by this setup."""
        return ["symbol", "close", "volume"]

    def signals(self, frame: pd.DataFrame) -> pd.Series:
        """Signal at close when the instrument dips sharply but stays in trend."""
        close = frame["close"]
        dip = rate_of_change(close, int(parameter_number(self.config, "fall_lookback")))
        trend = sma(close, int(parameter_number(self.config, "trend_sma_length")))
        threshold = parameter_number(self.config, "fall_threshold_pct")
        return (dip.le(-threshold) & close.gt(trend) & eligible_rows(frame, self.config)).fillna(
            False
        )
