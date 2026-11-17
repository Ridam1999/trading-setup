"""Validated configuration for the frozen factor-study design."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from nse_engine.config import config_directory


class Hypothesis(BaseModel):
    """One pre-registered cross-sectional hypothesis."""

    model_config = ConfigDict(extra="forbid")

    id: Literal["H1", "H2", "H3", "H4"]
    name: str
    definition: str
    expected_sign: Literal["positive", "negative"]

    @field_validator("definition", "name")
    @classmethod
    def non_empty(cls, value: str) -> str:
        """Reject blank hypothesis text."""
        if not value.strip():
            raise ValueError("Hypothesis text must be non-empty")
        return value


class FactorUniverseConfig(BaseModel):
    """Point-in-time universe rules."""

    model_config = ConfigDict(extra="forbid")

    size: int = Field(ge=1)
    liquidity_lookback_sessions: int = Field(ge=1)
    exclude_index_series: bool
    exclude_etfs: bool
    delisted_inclusive: bool


class FactorConstructionConfig(BaseModel):
    """Portfolio construction rules; lags enforce causal execution."""

    model_config = ConfigDict(extra="forbid")

    basket_fraction: float = Field(gt=0, le=1)
    rebalance_frequency: Literal["monthly", "quarterly"]
    max_one_way_turnover_fraction: float = Field(gt=0, le=1)
    equal_weight: bool
    signal_to_order_lag_sessions: int = Field(ge=1)


class FundamentalsConfig(BaseModel):
    """Announcement-dated fundamental rules."""

    model_config = ConfigDict(extra="forbid")

    announcement_lag_sessions: int = Field(ge=1)
    restatement_leak_test_required: bool


class FactorPriceConfig(BaseModel):
    """Momentum window and point-in-time price requirements."""

    model_config = ConfigDict(extra="forbid")

    momentum_skip_recent_sessions: int = Field(ge=1)
    momentum_lookback_sessions: int = Field(ge=1)
    requires_pit_delisted_inclusive_prices: bool


class FactorConfig(BaseModel):
    """Frozen design loaded from `config/factor.yaml`."""

    model_config = ConfigDict(extra="forbid")

    prereg_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    tuition_budget_inr: int = Field(ge=0)
    universe: FactorUniverseConfig
    construction: FactorConstructionConfig
    fundamentals: FundamentalsConfig
    price: FactorPriceConfig
    hypotheses: list[Hypothesis] = Field(min_length=1)
    winsorize_fraction: float = Field(ge=0, lt=0.5)
    benchmarks: list[str] = Field(min_length=1)
    horizons_months: list[int] = Field(min_length=1)
    fdr_alpha: float = Field(gt=0, lt=1)
    minimum_independent_rebalance_dates: int = Field(ge=1)
    cost_stress_multiplier: float = Field(ge=1)

    @field_validator("horizons_months")
    @classmethod
    def positive_horizons(cls, values: list[int]) -> list[int]:
        """Reject zero or negative forward horizons."""
        if any(value < 1 for value in values):
            raise ValueError("Horizons must be positive month counts")
        return values

    @model_validator(mode="after")
    def check_hypotheses(self) -> FactorConfig:
        """Require the four unique pre-registered hypotheses."""
        if len(self.hypotheses) != 4:
            raise ValueError("The frozen design requires exactly four hypotheses")
        identifiers = [item.id for item in self.hypotheses]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Hypothesis IDs must be unique")
        return self


FUNDAMENTAL_FIELDS: tuple[str, ...] = (
    "trailing_12m_diluted_eps",
    "trailing_12m_net_profit",
    "average_shareholders_equity",
)


class FundamentalFieldConfig(BaseModel):
    """Label patterns and unit for one pre-registered fundamental field."""

    model_config = ConfigDict(extra="forbid")

    labels: list[str] = Field(min_length=1)
    unit: Literal["rupees", "crore"]

    @field_validator("labels")
    @classmethod
    def non_empty_labels(cls, values: list[str]) -> list[str]:
        """Reject blank label patterns."""
        if any(not value.strip() for value in values):
            raise ValueError("Fundamental label patterns must be non-empty")
        return values


class FundamentalExtractionConfig(BaseModel):
    """Config-driven extraction rules for announcement-dated fundamentals."""

    model_config = ConfigDict(extra="forbid")

    fields: dict[str, FundamentalFieldConfig]
    crore_multiplier: float = Field(gt=0)
    label_value_max_chars: int = Field(ge=1)

    @model_validator(mode="after")
    def check_fields(self) -> FundamentalExtractionConfig:
        """Require exactly the three frozen fundamental fields."""
        if set(self.fields) != set(FUNDAMENTAL_FIELDS):
            raise ValueError("Fundamental fields must match the frozen study design")
        return self


class AcquisitionConfig(BaseModel):
    """Public acquisition settings for the PIT input bot."""

    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    udiff_start_date: date
    session_url: str
    legacy_url_template: str
    udiff_url_template: str
    announcement_url_template: str
    announcement_raw_dir: Path
    filing_dir: Path
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    announcement_chunk_days: int = Field(ge=1)
    request_delay_seconds: float = Field(ge=0)
    timeout_seconds: float = Field(gt=0)
    retry_attempts: int = Field(ge=1)
    exclude_symbol_pattern: str | None = None

    @field_validator("weekdays")
    @classmethod
    def valid_weekdays(cls, values: list[int]) -> list[int]:
        """Reject invalid Python weekday numbers."""
        if any(day < 0 or day > 6 for day in values):
            raise ValueError("Weekdays must use Python numbering from 0 to 6")
        return sorted(set(values))

    @model_validator(mode="after")
    def check_dates(self) -> AcquisitionConfig:
        """Require an ordered, non-empty acquisition window."""
        if self.start_date > self.end_date:
            raise ValueError("Acquisition start_date must not exceed end_date")
        if not self.start_date <= self.udiff_start_date <= self.end_date:
            raise ValueError("udiff_start_date must lie inside the acquisition window")
        return self


class FactorDataConfig(BaseModel):
    """Paths to point-in-time inputs and their public acquisition settings."""

    model_config = ConfigDict(extra="forbid")

    pit_prices_dir: Path | None = None
    announcement_index_path: Path | None = None
    fundamentals_path: Path | None = None
    membership_path: Path | None = None
    acquisition: AcquisitionConfig | None = None
    fundamentals: FundamentalExtractionConfig | None = None


def _load_yaml(path: Path) -> dict[str, object]:
    """Load a YAML mapping from an explicit path."""
    with path.open(encoding="utf-8") as config_file:
        loaded = yaml.safe_load(config_file)
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return loaded


def load_factor_config(directory: Path | None = None) -> FactorConfig:
    """Load and validate the frozen factor design."""
    path = (directory or config_directory()) / "factor.yaml"
    return FactorConfig.model_validate(_load_yaml(path))


def load_factor_data_config(directory: Path | None = None) -> FactorDataConfig:
    """Load and validate the point-in-time data locations."""
    path = (directory or config_directory()) / "factor_data.yaml"
    return FactorDataConfig.model_validate(_load_yaml(path))
