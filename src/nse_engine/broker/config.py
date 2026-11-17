"""Validated Phase 6 broker settings from YAML and environment variables."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

from nse_engine.config import config_directory

TRUTHY = {"1", "true", "yes", "on"}


class RateLimitConfig(BaseModel):
    """Token-bucket limits for one broker request class."""

    model_config = ConfigDict(extra="forbid")

    burst: int = Field(ge=1)
    refill_per_second: float = Field(gt=0)


class BrokerConfig(BaseModel):
    """Configured broker safety limits and credential environment names."""

    model_config = ConfigDict(extra="forbid")

    dry_run: bool = True
    live_confirm_env: str
    api_key_env: str
    api_secret_env: str
    totp_seed_env: str
    order_rate: RateLimitConfig
    data_rate: RateLimitConfig
    batch_ltp_max_symbols: int = Field(ge=1)
    global_kill_switch: bool = False
    daily_loss_cap_rupees: float = Field(gt=0)
    max_order_size_rupees: float = Field(gt=0)
    max_open_positions: int = Field(ge=1)

    @property
    def live_enabled(self) -> bool:
        """Return whether the owner explicitly enabled the live path this run."""
        return not self.dry_run and os.environ.get(self.live_confirm_env, "").lower() in TRUTHY


def load_broker_config(path: Path | None = None) -> BrokerConfig:
    """Load and validate broker settings from `config/broker.yaml`."""
    directory = config_directory(path)
    config_path = directory / "broker.yaml"
    if not config_path.is_file():
        raise FileNotFoundError(f"Missing broker.yaml in {directory}")
    with config_path.open(encoding="utf-8") as config_file:
        loaded = yaml.safe_load(config_file)
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected a YAML mapping in {config_path}")
    return BrokerConfig.model_validate(loaded)
