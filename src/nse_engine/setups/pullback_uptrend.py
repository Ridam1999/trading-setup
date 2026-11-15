"""Pullback after a short-term oversold move in a long-term uptrend."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.features.indicators import rsi_wilder, sma
from nse_engine.setups.base import SetupConfig, eligible_rows, parameter_number


class PullbackUptrend:
    """Require a long-term uptrend, short RSI, and configured liquidity."""

    name = "pullback_uptrend"

    def __init__(self, config: SetupConfig) -> None:
        """Store the validated parameter set."""
        self.config = config

    def required_features(self) -> list[str]:
        """List input OHLCV columns used by this setup."""
        return ["close", "volume"]

    def signals(self, frame: pd.DataFrame) -> pd.Series:
        """Signal at close when the trend is up and RSI is below its YAML threshold."""
        trend = sma(frame["close"], int(parameter_number(self.config, "trend_sma_length")))
        momentum = rsi_wilder(frame["close"], int(parameter_number(self.config, "rsi_window")))
        threshold = parameter_number(self.config, "rsi_max")
        return (
            frame["close"].gt(trend) & momentum.lt(threshold) & eligible_rows(frame, self.config)
        ).fillna(False)
