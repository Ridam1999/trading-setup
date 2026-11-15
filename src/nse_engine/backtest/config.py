"""Validated portfolio and walk-forward settings from backtest.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from nse_engine.config import config_directory


class BacktestSettings(BaseModel):
    """Risk, portfolio, and robustness parameters for daily simulations."""

    model_config = ConfigDict(extra="forbid")

    initial_capital: float = Field(gt=0)
    risk_per_trade_fraction: float = Field(gt=0, lt=1)
    atr_lookback: int = Field(ge=2)
    stop_atr_multiple: float = Field(gt=0)
    default_stop_loss_pct: float = Field(gt=0, le=100)
    default_target_pct: float | None = Field(default=None, gt=0)
    maximum_holding_days: int = Field(ge=1)
    maximum_positions: int = Field(ge=1)
    maximum_symbol_weight: float = Field(gt=0, le=1)
    maximum_sector_weight: float = Field(gt=0, le=1)
    entry_limit_buffer_bps: float = Field(ge=0)
    annual_risk_free_rate: float = Field(ge=0, lt=1)
    benchmark_symbol: str
    monte_carlo_replicates: int = Field(ge=1)
    walk_forward_train_years: int = Field(ge=1)
    walk_forward_test_years: int = Field(ge=1)
    walk_forward_step_years: int = Field(ge=1)
    symbol_sectors: dict[str, str] = Field(default_factory=dict)
    sectors: dict[str, list[str]] = Field(default_factory=dict)
    reports_dir: Path


def load_backtest_settings(directory: Path | None = None) -> BacktestSettings:
    """Load and validate the portfolio simulation settings."""
    path = (directory or config_directory()) / "backtest.yaml"
    with path.open(encoding="utf-8") as config_file:
        document = yaml.safe_load(config_file)
    if not isinstance(document, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return BacktestSettings.model_validate(document)
