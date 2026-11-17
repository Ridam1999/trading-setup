"""Validated paper-trading settings and approved-setup loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from nse_engine.config import config_directory
from nse_engine.setups.base import SetupConfig
from nse_engine.setups.registry import create_setup


class DecaySettings(BaseModel):
    """Thresholds that suspend a setup whose live results leave its validated band."""

    model_config = ConfigDict(extra="forbid")

    minimum_trades: int = Field(ge=1)
    drawdown_tolerance: float = Field(gt=0)
    hit_rate_tolerance: float = Field(gt=0)
    consecutive_breaches: int = Field(ge=1)

    @field_validator("hit_rate_tolerance")
    @classmethod
    def tolerance_at_most_one(cls, value: float) -> float:
        """Reject a hit-rate tolerance that cannot represent a fraction."""
        if value > 1:
            raise ValueError("hit_rate_tolerance must be at most 1")
        return value


class PaperSettings(BaseModel):
    """Paths and decay policy for the daily paper-trading runner."""

    model_config = ConfigDict(extra="forbid")

    reports_dir: Path
    ledger_path: Path
    decay: DecaySettings


def load_paper_settings(directory: Path | None = None) -> PaperSettings:
    """Load and validate paper.yaml from the configured project directory."""
    path = (directory or config_directory()) / "paper.yaml"
    with path.open(encoding="utf-8") as config_file:
        document = yaml.safe_load(config_file)
    if not isinstance(document, dict):
        raise ValueError(f"Expected a YAML mapping in {path}")
    return PaperSettings.model_validate(document)


def load_approval_entries(directory: Path | None = None) -> list[dict[str, Any]]:
    """Return raw approval records, including the validated metric band."""
    path = (directory or config_directory()) / "approved_setups.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Missing {path}; run the Phase 2 study report first")
    with path.open(encoding="utf-8") as config_file:
        document: Any = yaml.safe_load(config_file)
    entries = document.get("approved_setups", []) if isinstance(document, dict) else []
    if not isinstance(entries, list):
        raise ValueError(f"approved_setups in {path} must be a list")
    if any(not isinstance(entry, dict) for entry in entries):
        raise ValueError(f"approved_setups in {path} must contain mappings")
    return [dict(entry) for entry in entries]


def load_approved_setups(directory: Path | None = None) -> list[SetupConfig]:
    """Return reviewed Phase 2 setups; an empty approval file is a valid no-trade state."""
    entries = load_approval_entries(directory)
    setups = [SetupConfig.model_validate(entry["parameters"]) for entry in entries]
    return [create_setup(setup).config for setup in setups]
