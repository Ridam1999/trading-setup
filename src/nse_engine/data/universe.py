"""Load static or point-in-time universe memberships from YAML."""

from __future__ import annotations

from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]
import yaml


def load_universe(path: Path) -> pd.DataFrame:
    """Load configured symbols and annotate static membership survivorship bias."""
    with path.open(encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    if not isinstance(config, dict):
        raise ValueError(f"Expected a mapping in {path}")
    symbols = config.get("symbols", [])
    if not isinstance(symbols, list):
        raise ValueError("Universe symbols must be a list")
    return pd.DataFrame(
        {
            "symbol": [str(symbol).strip().upper() for symbol in symbols],
            "index_name": config.get("name", "configured_universe"),
            "start_date": None,
            "end_date": None,
            "membership_type": "static_biased",
        }
    )
