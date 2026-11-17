"""Validated display settings for the Phase 5 Streamlit dashboard."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from nse_engine.config import config_directory


class UISettings(BaseModel):
    """Non-research limits that bound dashboard rendering and sampling."""

    model_config = ConfigDict(extra="forbid")

    default_symbol: str
    min_bars: int = Field(ge=1)
    default_bars: int = Field(ge=1)
    max_bars: int = Field(ge=1)
    table_rows: int = Field(ge=1)
    event_sample_rows: int = Field(ge=1)


def load_ui_settings(directory: Path | None = None) -> UISettings:
    """Load and validate `config/ui.yaml`."""
    config_path = (directory or config_directory()) / "ui.yaml"
    with config_path.open(encoding="utf-8") as config_file:
        return UISettings.model_validate(yaml.safe_load(config_file))
