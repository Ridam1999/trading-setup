"""Breakout following a recent volatility contraction."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.features.indicators import rolling_high, volatility_contraction_ratio
from nse_engine.setups.base import SetupConfig, eligible_rows, parameter_number


class VolatilityContraction:
    """Signal a range break after the configured ATR contraction."""

    name = "vol_contraction"

    def __init__(self, config: SetupConfig) -> None:
        """Store the validated parameter set."""
        self.config = config

    def required_features(self) -> list[str]:
        """List input OHLCV columns used by this setup."""
        return ["high", "low", "close", "volume"]

    def signals(self, frame: pd.DataFrame) -> pd.Series:
        """Signal at close on a range breakout after recent compressed ATR."""
        contraction = volatility_contraction_ratio(
            frame["high"],
            frame["low"],
            frame["close"],
            int(parameter_number(self.config, "atr_short_window")),
            int(parameter_number(self.config, "atr_long_window")),
        )
        recent_contraction = (
            contraction.shift(1)
            .rolling(int(parameter_number(self.config, "contraction_lookback")), min_periods=1)
            .min()
        )
        previous_range_high = rolling_high(
            frame["high"].shift(1), int(parameter_number(self.config, "breakout_window"))
        )
        return (
            recent_contraction.lt(parameter_number(self.config, "contraction_max"))
            & frame["close"].gt(previous_range_high)
            & eligible_rows(frame, self.config)
        ).fillna(False)
