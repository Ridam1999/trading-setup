"""Setup interface, configuration, and common liquidity/price filters."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import pandas as pd  # type: ignore[import-untyped]
import yaml
from pydantic import BaseModel, ConfigDict, Field

from nse_engine.features.indicators import average_traded_value


class SetupConfig(BaseModel):
    """Validated configuration for a registered rule-based setup."""

    model_config = ConfigDict(extra="forbid")

    name: str
    universe: str
    symbols: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    params: dict[str, int | float | str | bool]
    filters: dict[str, int | float | str | bool]
    exit_rules: dict[str, int | float | str | bool]
    sweep_grid: dict[str, list[int | float | str | bool]] = Field(default_factory=dict)


class Setup(Protocol):
    """A close-of-day rule that returns one boolean signal per input row."""

    config: SetupConfig

    def __init__(self, config: SetupConfig) -> None:
        """Construct a setup from its validated configuration."""

    def required_features(self) -> list[str]:
        """List raw columns required to evaluate this setup."""

    def signals(self, frame: pd.DataFrame) -> pd.Series:
        """Return signals evaluated using data available by each row's close."""


def load_setup_config(name: str, directory: Path = Path("config/setups")) -> SetupConfig:
    """Load a setup YAML file from the declared setup configuration directory."""
    if not name.replace("_", "").isalnum():
        raise ValueError("Setup name must contain only letters, digits, and underscores")
    path = directory / f"{name}.yaml"
    with path.open(encoding="utf-8") as setup_file:
        data = yaml.safe_load(setup_file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    config = SetupConfig.model_validate(data)
    if config.name != name:
        raise ValueError(f"Setup file {path} declares name {config.name!r}")
    return config


def parameter_number(config: SetupConfig, name: str) -> float:
    """Read a numeric indicator parameter from validated YAML configuration."""
    value = config.params.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"Setup parameter {name!r} must be numeric")
    return float(value)


def filter_number(config: SetupConfig, name: str) -> float:
    """Read a numeric filter threshold from validated YAML configuration."""
    value = config.filters.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"Setup filter {name!r} must be numeric")
    return float(value)


def eligible_rows(frame: pd.DataFrame, config: SetupConfig) -> pd.Series:
    """Apply configured minimum price and average traded-value screens."""
    price_floor = filter_number(config, "minimum_price")
    traded_value_floor = filter_number(config, "minimum_average_traded_value")
    traded_value_window = int(parameter_number(config, "traded_value_window"))
    mean_value = average_traded_value(frame["close"], frame["volume"], traded_value_window)
    eligible = frame["close"].ge(price_floor) & mean_value.ge(traded_value_floor)
    if config.symbols and "symbol" in frame:
        eligible &= frame["symbol"].isin(config.symbols)
    return eligible
