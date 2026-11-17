"""Focused behavior and look-ahead tests for the two new Phase 2 setups."""

from pathlib import Path

import numpy as np
import pandas as pd

from nse_engine.setups.base import SetupConfig, load_setup_config
from nse_engine.setups.registry import SETUP_REGISTRY, create_setup


def _delivery_frame(length: int = 320) -> pd.DataFrame:
    """Build a deterministic price frame with a single delivery spike."""
    step = np.arange(length, dtype=float)
    close = 100 + step * 0.1
    delivery = pd.Series(20.0, index=range(length))
    delivery.iloc[250] = 60.0
    return pd.DataFrame(
        {
            "open": close - 0.5,
            "close": close,
            "volume": 100000.0,
            "delivery_pct": delivery,
        }
    )


def _relative_strength_frame(length: int = 320) -> pd.DataFrame:
    """Build a frame with constant-benchmark volatility and a faster stock."""
    step = np.arange(length, dtype=float)
    return pd.DataFrame(
        {
            "close": 100 * 1.003**step,
            "volume": 100000.0,
            "_benchmark_close": 100 * 1.001**step,
        }
    )


def test_both_new_setups_are_registered_with_predeclared_grids() -> None:
    """The two hypotheses are reachable only through the explicit class registry."""
    for name in ("delivery_spike", "relative_strength_regime"):
        assert name in SETUP_REGISTRY
        config = load_setup_config(name, Path("config/setups"))
        assert config.rationale
        assert config.sweep_grid
        assert SETUP_REGISTRY[name](config).name == name


def test_delivery_spike_signals_only_on_unusual_delivery() -> None:
    """A delivery spike above its trailing average on an up-close fires once."""
    config = load_setup_config("delivery_spike", Path("config/setups"))
    signals = create_setup(config).signals(_delivery_frame())

    assert signals.iloc[250]
    assert not signals.iloc[249]
    assert int(signals.sum()) == 1


def test_delivery_spike_is_inert_without_delivery_history() -> None:
    """All-null delivery data produces no signals rather than fabricated ones."""
    frame = _delivery_frame()
    frame["delivery_pct"] = pd.Series([None] * len(frame), dtype=object)
    config = load_setup_config("delivery_spike", Path("config/setups"))

    assert not create_setup(config).signals(frame).any()


def test_delivery_spike_ignores_future_bars() -> None:
    """Changing bars after t never changes the delivery signal at or before t."""
    original = _delivery_frame()
    altered = original.copy()
    stop = 260
    altered.loc[stop:, ["open", "close", "delivery_pct"]] *= 3.0
    config = load_setup_config("delivery_spike", Path("config/setups"))

    pd.testing.assert_series_equal(
        create_setup(config).signals(original).iloc[:stop],
        create_setup(config).signals(altered).iloc[:stop],
        check_names=False,
    )


def test_relative_strength_regime_signals_excess_in_benign_regime() -> None:
    """Outperformance above the configured threshold fires after regime warm-up."""
    config = load_setup_config("relative_strength_regime", Path("config/setups"))
    signals = create_setup(config).signals(_relative_strength_frame())

    assert not signals.iloc[10]
    assert signals.iloc[250]
    assert int(signals.sum()) > 1


def test_relative_strength_regime_ignores_future_bars() -> None:
    """Changing later stock and benchmark bars never changes an earlier signal."""
    original = _relative_strength_frame()
    altered = original.copy()
    stop = 260
    altered.loc[stop:, ["close", "_benchmark_close"]] *= 3.0
    config = load_setup_config("relative_strength_regime", Path("config/setups"))

    pd.testing.assert_series_equal(
        create_setup(config).signals(original).iloc[:stop],
        create_setup(config).signals(altered).iloc[:stop],
        check_names=False,
    )


def test_configuration_override_is_accepted_by_the_registry() -> None:
    """Sweep parameter overrides are read from the config, never hidden in code."""
    base = load_setup_config("relative_strength_regime", Path("config/setups"))
    override = base.model_copy(update={"params": {**base.params, "rs_min_excess_pct": 10.0}})

    assert not create_setup(override).signals(_relative_strength_frame()).any()

    config = SetupConfig(**base.model_dump())
    assert config.params["benchmark_trend_length"] == 200
