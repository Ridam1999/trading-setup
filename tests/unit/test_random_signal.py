"""Deterministic negative-control setup tests."""

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from nse_engine.cli import _study_warmup_bars
from nse_engine.setups.base import SetupConfig, load_setup_config
from nse_engine.setups.random_signal import RandomSignal


def _frame(symbols: list[str], sessions: int) -> pd.DataFrame:
    """Build a simple OHLCV frame with deterministic session dates."""
    start = date(2024, 1, 1)
    rows = [
        {
            "symbol": symbol,
            "date": start + timedelta(days=index),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100000,
        }
        for symbol in symbols
        for index in range(sessions)
    ]
    return pd.DataFrame(rows)


def _config(probability: float = 0.25, seed: int = 7) -> SetupConfig:
    """Return a controlled negative-control configuration."""
    return SetupConfig(
        name="random_signal",
        universe="nse_equities",
        rationale="A deterministic negative control.",
        params={
            "seed": seed,
            "signal_probability": probability,
            "traded_value_window": 1,
        },
        filters={"minimum_price": 1, "minimum_average_traded_value": 0},
        exit_rules={},
    )


def test_random_signal_ignores_future_bars_and_is_reproducible() -> None:
    """Truncating or changing later bars leaves earlier signals unchanged."""
    original = _frame(["AAA", "BBB"], 240)
    altered = original.copy()
    altered.loc[240:, ["open", "high", "low", "close"]] *= 3
    setup = RandomSignal(_config())

    full = setup.signals(original)
    truncated = setup.signals(original.iloc[:120])
    changed = setup.signals(altered)

    pd.testing.assert_series_equal(truncated, full.iloc[:120], check_names=False)
    pd.testing.assert_series_equal(full, changed, check_names=False)
    pd.testing.assert_series_equal(full, setup.signals(original), check_names=False)


def test_random_signal_rate_follows_configured_probability() -> None:
    """A seeded control fires near its declared rate rather than following prices."""
    signals = RandomSignal(_config()).signals(_frame(["AAA", "BBB", "CCC"], 400))
    rate = float(signals.mean())

    assert 0.20 < rate < 0.30


def test_registered_config_has_a_seed_and_is_registered() -> None:
    """The checked-in negative control loads and registers under its YAML name."""
    config = load_setup_config("random_signal", Path("config/setups"))

    assert isinstance(config.params["seed"], int)
    assert RandomSignal(config).name == "random_signal"
    assert _study_warmup_bars(config) == 20


def test_random_signal_rejects_invalid_parameters() -> None:
    """A missing seed or out-of-range probability fails loudly at construction."""
    bad_seed = _config().model_copy(update={"params": {"signal_probability": 0.1}})
    bad_probability = _config().model_copy(
        update={"params": {"seed": 1, "signal_probability": 1.5}}
    )

    with pytest.raises(ValueError, match="seed"):
        RandomSignal(bad_seed)
    with pytest.raises(ValueError, match="probability"):
        RandomSignal(bad_probability)
