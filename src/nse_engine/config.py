"""Load and validate the project's YAML configuration files."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class BaseConfig(BaseModel):
    """Shared application configuration values."""

    model_config = ConfigDict(extra="forbid")

    app_name: str
    timezone: str
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    random_seed: int = Field(ge=0)


class ProjectConfig(BaseModel):
    """Validated base settings and the remaining phase-specific YAML documents."""

    base: BaseConfig
    documents: dict[str, dict[str, Any]]


class DataConfig(BaseModel):
    """Validated data paths and quality/download settings."""

    raw_dir: Path
    adjusted_dir: Path
    duckdb_path: Path
    reports_dir: Path
    minimum_analysis_date: date
    maximum_single_day_return_pct: float = Field(gt=0)
    maximum_adjusted_raw_difference_pct: float = Field(gt=0)
    stale_close_days: int = Field(ge=1)
    retry_attempts: int = Field(ge=1)
    retry_wait_seconds: float = Field(gt=0)
    request_timeout_seconds: float = Field(gt=0)
    nse_bhavcopy_url: str | None = None
    corporate_actions_path: Path | None = None
    pit_membership_path: Path | None = None
    require_corporate_actions: bool = True
    require_pit_membership: bool = True
    holidays: list[date] = Field(default_factory=list)


DataBiasStatus = Literal["SURVIVOR_BIASED", "UNBIASED"]


def load_data_bias_status(config: ProjectConfig | None = None) -> DataBiasStatus:
    """Return the declared research data-bias status, defaulting to survivor-biased."""
    project = config or load_config()
    document = project.documents.get("research", {})
    status = document.get("data_bias_status", "SURVIVOR_BIASED")
    if status == "SURVIVOR_BIASED":
        return "SURVIVOR_BIASED"
    if status == "UNBIASED":
        return "UNBIASED"
    raise ValueError("research.data_bias_status must be SURVIVOR_BIASED or UNBIASED")


def config_directory(path: Path | None = None) -> Path:
    """Resolve the config directory from an argument, environment, or project default."""
    if path is not None:
        return path
    configured = os.environ.get("NSE_ENGINE_CONFIG_DIR")
    if configured:
        return Path(configured)
    return Path("config")


def load_config(path: Path | None = None) -> ProjectConfig:
    """Load all top-level YAML documents and validate the shared base document."""
    directory = config_directory(path)
    if not directory.is_dir():
        raise FileNotFoundError(f"Configuration directory does not exist: {directory}")

    documents: dict[str, dict[str, Any]] = {}
    for yaml_path in sorted((*directory.glob("*.yaml"), *directory.glob("*.yml"))):
        with yaml_path.open(encoding="utf-8") as config_file:
            loaded = yaml.safe_load(config_file)
        if not isinstance(loaded, dict):
            raise ValueError(f"Expected a YAML mapping in {yaml_path}")
        documents[yaml_path.stem] = loaded

    if "base" not in documents:
        raise ValueError(f"Missing required base.yaml in {directory}")
    base = BaseConfig.model_validate(documents.pop("base"))
    return ProjectConfig(base=base, documents=documents)


def load_data_config(path: Path | None = None) -> DataConfig:
    """Load and validate Phase 1 settings from `data.yaml`."""
    directory = config_directory(path)
    config_path = directory / "data.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"Missing data.yaml in {directory}")
    with config_path.open(encoding="utf-8") as config_file:
        loaded = yaml.safe_load(config_file)
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected a YAML mapping in {config_path}")
    return DataConfig.model_validate(loaded)
