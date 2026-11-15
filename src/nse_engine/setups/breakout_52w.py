"""Close at a trailing-year high on above-average volume."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.features.indicators import average_volume, rolling_high
from nse_engine.setups.base import SetupConfig, eligible_rows, parameter_number


class Breakout52Week:
    """Signal a trailing high when current volume exceeds its configured average."""

    name = "breakout_52w"

    def __init__(self, config: SetupConfig) -> None:
        """Store the validated parameter set."""
        self.config = config

    def required_features(self) -> list[str]:
        """List input OHLCV columns used by this setup."""
        return ["close", "high", "volume"]

    def signals(self, frame: pd.DataFrame) -> pd.Series:
        """Signal at close when price reaches its lookback high with volume confirmation."""
        high_length = int(parameter_number(self.config, "high_lookback"))
        volume_length = int(parameter_number(self.config, "volume_average_window"))
        volume_multiple = parameter_number(self.config, "volume_multiple")
        high = rolling_high(frame["high"], high_length)
        average = average_volume(frame["volume"], volume_length)
        return (
            frame["close"].ge(high)
            & frame["volume"].gt(average * volume_multiple)
            & eligible_rows(frame, self.config)
        ).fillna(False)
