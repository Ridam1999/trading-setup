"""Pure, per-symbol indicator functions using data available at each row."""

from __future__ import annotations

import numpy as np
import pandas as pd  # type: ignore[import-untyped]


def _validate_length(length: int) -> None:
    """Reject invalid rolling lengths."""
    if length < 1:
        raise ValueError("Indicator length must be positive")


def sma(values: pd.Series, length: int) -> pd.Series:
    """Simple moving average, available after a full trailing window."""
    _validate_length(length)
    return values.rolling(length, min_periods=length).mean()


def ema(values: pd.Series, length: int) -> pd.Series:
    """Exponential moving average with a full-window warm-up."""
    _validate_length(length)
    return values.ewm(span=length, min_periods=length, adjust=False).mean()


def rsi_wilder(close: pd.Series, length: int) -> pd.Series:
    """Wilder relative-strength index based on trailing gains and losses."""
    _validate_length(length)
    change = close.diff()
    gains = change.clip(lower=0)
    losses = -change.clip(upper=0)
    average_gain = gains.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    average_loss = losses.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    relative_strength = average_gain.div(average_loss.where(average_loss.ne(0)))
    result = 100 - 100 / (1 + relative_strength)
    result = result.mask(average_loss.eq(0) & average_gain.gt(0), 100.0)
    result = result.mask(average_loss.eq(0) & average_gain.eq(0), 50.0)
    return result


def atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    """Wilder average true range, including the prior-close gap component."""
    _validate_length(length)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1, skipna=True)
    return true_range.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()


def rolling_high(values: pd.Series, length: int) -> pd.Series:
    """Trailing rolling high, including the current bar."""
    _validate_length(length)
    return values.rolling(length, min_periods=length).max()


def rolling_low(values: pd.Series, length: int) -> pd.Series:
    """Trailing rolling low, including the current bar."""
    _validate_length(length)
    return values.rolling(length, min_periods=length).min()


def average_volume(volume: pd.Series, length: int) -> pd.Series:
    """Trailing average daily volume."""
    return sma(volume, length)


def average_traded_value(close: pd.Series, volume: pd.Series, length: int) -> pd.Series:
    """Trailing mean daily traded value in rupees."""
    return sma(close * volume, length)


def rate_of_change(values: pd.Series, length: int) -> pd.Series:
    """Percentage change versus the close `length` observations earlier."""
    _validate_length(length)
    return values.div(values.shift(length)).sub(1).mul(100)


def percentile_rank(values: pd.Series, length: int) -> pd.Series:
    """Trailing empirical percentile rank of the current value, including itself."""
    _validate_length(length)
    return values.rolling(length, min_periods=length).apply(
        lambda window: float(np.count_nonzero(window <= window[-1]) / length), raw=True
    )


def bollinger_bands(close: pd.Series, length: int, standard_deviations: float) -> pd.DataFrame:
    """Return trailing lower, middle, and upper Bollinger bands."""
    _validate_length(length)
    if standard_deviations <= 0:
        raise ValueError("Bollinger standard deviations must be positive")
    middle = sma(close, length)
    deviation = close.rolling(length, min_periods=length).std(ddof=0)
    return pd.DataFrame(
        {
            "lower": middle - standard_deviations * deviation,
            "middle": middle,
            "upper": middle + standard_deviations * deviation,
        },
        index=close.index,
    )


def volatility_contraction_ratio(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    short_length: int,
    long_length: int,
) -> pd.Series:
    """Ratio of short Wilder ATR to long Wilder ATR."""
    if short_length >= long_length:
        raise ValueError("Short ATR length must be less than long ATR length")
    short_atr = atr(high, low, close, short_length)
    long_atr = atr(high, low, close, long_length)
    return short_atr.div(long_atr.where(long_atr.ne(0)))


def distance_from_high(close: pd.Series, length: int) -> pd.Series:
    """Percent distance from the trailing high, with the current bar included."""
    return close.div(rolling_high(close, length)).sub(1).mul(100)


def relative_strength(close: pd.Series, benchmark_close: pd.Series) -> pd.Series:
    """Normalized price ratio to a same-index benchmark series."""
    aligned_benchmark = benchmark_close.reindex(close.index)
    return close.div(close.iloc[0]).div(aligned_benchmark.div(aligned_benchmark.iloc[0]))


def regime_labels(
    benchmark_close: pd.Series, trend_length: int, volatility_length: int
) -> pd.DataFrame:
    """Create causal trend and expanding-volatility-tercile labels for a benchmark."""
    _validate_length(trend_length)
    _validate_length(volatility_length)
    trend = benchmark_close > sma(benchmark_close, trend_length)
    volatility = (
        rate_of_change(benchmark_close, 1)
        .rolling(volatility_length, min_periods=volatility_length)
        .std(ddof=0)
    )
    lower_threshold = volatility.expanding(min_periods=volatility_length).quantile(1 / 3)
    upper_threshold = volatility.expanding(min_periods=volatility_length).quantile(2 / 3)
    tercile = pd.Series(index=volatility.index, dtype="string")
    tercile.loc[volatility.le(lower_threshold)] = "low"
    tercile.loc[volatility.gt(lower_threshold) & volatility.le(upper_threshold)] = "middle"
    tercile.loc[volatility.gt(upper_threshold)] = "high"
    return pd.DataFrame(
        {
            "trend_regime": trend.map({True: "above_sma", False: "below_sma"}),
            "volatility_regime": tercile,
        },
        index=benchmark_close.index,
    )
