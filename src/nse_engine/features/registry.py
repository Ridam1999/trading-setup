"""Explicit registry of reusable indicator functions."""

from nse_engine.features.indicators import (
    atr,
    average_traded_value,
    average_volume,
    bollinger_bands,
    distance_from_high,
    ema,
    percentile_rank,
    rate_of_change,
    relative_strength,
    rolling_high,
    rolling_low,
    rsi_wilder,
    sma,
    volatility_contraction_ratio,
)

INDICATOR_REGISTRY = {
    "sma": sma,
    "ema": ema,
    "rsi": rsi_wilder,
    "atr": atr,
    "rolling_high": rolling_high,
    "rolling_low": rolling_low,
    "average_volume": average_volume,
    "average_traded_value": average_traded_value,
    "rate_of_change": rate_of_change,
    "percentile_rank": percentile_rank,
    "bollinger_bands": bollinger_bands,
    "volatility_contraction_ratio": volatility_contraction_ratio,
    "distance_from_high": distance_from_high,
    "relative_strength": relative_strength,
}
