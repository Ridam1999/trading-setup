"""Validated event-study and gate settings from YAML."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from nse_engine.config import config_directory


class StudySettings(BaseModel):
    """Parameters shared across event studies."""

    model_config = ConfigDict(extra="forbid")

    horizons: list[int] = Field(min_length=1)
    entry_limit_buffer_bps: float = Field(ge=0)
    notional_rupees: float = Field(gt=0)
    bootstrap_replicates: int = Field(ge=1)
    bootstrap_block_length: int = Field(ge=1)
    fdr_alpha: float = Field(gt=0, lt=1)
    benchmark_symbol: str
    benchmark_trend_length: int = Field(ge=1)
    volatility_lookback: int = Field(ge=1)
    trial_registry_path: Path
    events_dir: Path
    provisional_universe_path: Path = Path("config/universe_provisional.yaml")
    provisional_report_path: Path = Path("reports/study_report_provisional.html")


class GateSettings(BaseModel):
    """Validation thresholds used to approve or reject setup hypotheses."""

    model_config = ConfigDict(extra="forbid")

    minimum_independent_dates: int = Field(ge=1)
    minimum_positive_year_fraction: float = Field(ge=0, le=1)
    minimum_positive_symbol_fraction: float = Field(ge=0, le=1)
    maximum_single_year_profit_fraction: float = Field(gt=0, le=1)
    minimum_cost_stress_multiplier: float = Field(ge=1)
    maximum_adjusted_p_value: float = Field(gt=0, lt=1)
    require_positive_parameter_neighbours: bool


def load_study_settings(directory: Path | None = None) -> StudySettings:
    """Load study.yaml from a config directory."""
    config_path = (directory or config_directory()) / "study.yaml"
    with config_path.open(encoding="utf-8") as config_file:
        return StudySettings.model_validate(yaml.safe_load(config_file))


def load_gate_settings(directory: Path | None = None) -> GateSettings:
    """Load gates.yaml from a config directory."""
    config_path = (directory or config_directory()) / "gates.yaml"
    with config_path.open(encoding="utf-8") as config_file:
        return GateSettings.model_validate(yaml.safe_load(config_file))
