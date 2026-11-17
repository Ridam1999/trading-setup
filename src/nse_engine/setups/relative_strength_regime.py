"""Benchmark-relative strength filtered by a causal market-regime label."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]

from nse_engine.features.indicators import rate_of_change, regime_labels
from nse_engine.setups.base import SetupConfig, eligible_rows, parameter_number


class RelativeStrengthRegime:
    """Buy stocks outperforming the benchmark while the benchmark regime is benign."""

    name = "relative_strength_regime"

    def __init__(self, config: SetupConfig) -> None:
        """Store the validated parameter set."""
        self.config = config

    def required_features(self) -> list[str]:
        """List input columns; the benchmark close is injected by the event builder."""
        return ["close", "_benchmark_close"]

    def signals(self, frame: pd.DataFrame) -> pd.Series:
        """Signal at close on excess trailing return in a non-stressed market regime."""
        lookback = int(parameter_number(self.config, "rs_lookback"))
        minimum_excess = parameter_number(self.config, "rs_min_excess_pct")
        benchmark = pd.to_numeric(frame["_benchmark_close"], errors="coerce")
        excess = rate_of_change(frame["close"], lookback) - rate_of_change(benchmark, lookback)
        labels = regime_labels(
            benchmark,
            int(parameter_number(self.config, "benchmark_trend_length")),
            int(parameter_number(self.config, "benchmark_volatility_length")),
        )
        benign = labels["trend_regime"].eq("above_sma") & labels["volatility_regime"].isin(
            ["low", "middle"]
        )
        return (excess.ge(minimum_excess) & benign & eligible_rows(frame, self.config)).fillna(
            False
        )
