"""Unusually high settled volume on an up-close as evidence of accumulation."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.features.indicators import sma
from nse_engine.setups.base import SetupConfig, eligible_rows, parameter_number


class DeliverySpike:
    """Signal when delivery rises above its trailing average on a positive close."""

    name = "delivery_spike"

    def __init__(self, config: SetupConfig) -> None:
        """Store the validated parameter set."""
        self.config = config

    def required_features(self) -> list[str]:
        """List input OHLCV and delivery columns used by this setup."""
        return ["open", "close", "delivery_pct"]

    def signals(self, frame: pd.DataFrame) -> pd.Series:
        """Signal at close when settled volume is unusually high on an up-close."""
        window = int(parameter_number(self.config, "delivery_average_window"))
        multiple = parameter_number(self.config, "delivery_multiple")
        delivery = pd.to_numeric(frame["delivery_pct"], errors="coerce")
        baseline = sma(delivery, window).shift(1)
        spike = delivery.gt(baseline * multiple)
        positive_close = frame["close"].gt(frame["open"])
        return (spike & positive_close & eligible_rows(frame, self.config)).fillna(False)
