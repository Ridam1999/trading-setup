"""Past-only behavior tests for every Phase 2 indicator and starter setup."""

from pathlib import Path

import numpy as np
import pandas as pd

from nse_engine.features import indicators
from nse_engine.setups.base import load_setup_config
from nse_engine.setups.registry import SETUP_REGISTRY


def _indicator_outputs(frame: pd.DataFrame) -> dict[str, pd.Series | pd.DataFrame]:
    """Evaluate each registered indicator with fixed parameters."""
    close, high, low, volume = frame["close"], frame["high"], frame["low"], frame["volume"]
    return {
        "sma": indicators.sma(close, 17),
        "ema": indicators.ema(close, 17),
        "rsi": indicators.rsi_wilder(close, 7),
        "atr": indicators.atr(high, low, close, 14),
        "rolling_high": indicators.rolling_high(high, 17),
        "rolling_low": indicators.rolling_low(low, 17),
        "average_volume": indicators.average_volume(volume, 17),
        "average_traded_value": indicators.average_traded_value(close, volume, 17),
        "rate_of_change": indicators.rate_of_change(close, 17),
        "percentile_rank": indicators.percentile_rank(close, 17),
        "bollinger_bands": indicators.bollinger_bands(close, 17, 2.0),
        "volatility_contraction_ratio": indicators.volatility_contraction_ratio(
            high, low, close, 10, 30
        ),
        "distance_from_high": indicators.distance_from_high(close, 30),
        "relative_strength": indicators.relative_strength(close, frame["benchmark"]),
        "regime_labels": indicators.regime_labels(frame["benchmark"], 30, 10),
    }


def _synthetic_frame(length: int = 400) -> pd.DataFrame:
    """Build a deterministic OHLCV series with enough history for long windows."""
    step = np.arange(length, dtype=float)
    close = 100 + step * 0.2 + np.sin(step / 5)
    return pd.DataFrame(
        {
            "symbol": "TEST",
            "date": pd.date_range("2020-01-01", periods=length, freq="B").date,
            "open": close - 0.1,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 100000 + step,
            "benchmark": 100 + step * 0.12 + np.cos(step / 9),
        }
    )


def _assert_prefix_equal(
    left: pd.Series | pd.DataFrame, right: pd.Series | pd.DataFrame, stop: int
) -> None:
    """Compare output only through the last unchanged input observation."""
    left_prefix = left.iloc[:stop]
    right_prefix = right.iloc[:stop]
    if isinstance(left_prefix, pd.DataFrame):
        pd.testing.assert_frame_equal(left_prefix, right_prefix, check_exact=False, rtol=1e-12)
    else:
        pd.testing.assert_series_equal(left_prefix, right_prefix, check_exact=False, rtol=1e-12)


def test_every_indicator_is_invariant_to_future_data() -> None:
    """Changing all bars after t never changes an indicator value at or before t."""
    original = _synthetic_frame()
    altered = original.copy()
    stop = 320
    altered.loc[stop:, ["open", "high", "low", "close", "benchmark"]] *= 2.5
    for name, output in _indicator_outputs(original).items():
        _assert_prefix_equal(output, _indicator_outputs(altered)[name], stop)


def test_every_registered_setup_is_invariant_to_future_bars() -> None:
    """Every starter setup gives identical past signals after later bars change."""
    original = _synthetic_frame()
    altered = original.copy()
    stop = 320
    altered.loc[stop:, ["open", "high", "low", "close"]] *= 2.5
    for name, setup_type in SETUP_REGISTRY.items():
        config = load_setup_config(name, Path("config/setups"))
        original_signals = setup_type(config).signals(original)
        altered_signals = setup_type(config).signals(altered)
        pd.testing.assert_series_equal(
            original_signals.iloc[:stop], altered_signals.iloc[:stop], check_names=False
        )
