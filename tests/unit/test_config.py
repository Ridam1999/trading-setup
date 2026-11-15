"""Tests for Phase 0 configuration loading."""

from pathlib import Path

from nse_engine.config import load_config


def test_loads_project_configuration() -> None:
    """The supplied project YAML files validate and are discoverable."""
    config = load_config(Path("config"))

    assert config.base.timezone == "Asia/Kolkata"
    assert config.base.random_seed == 20261115
    assert {"costs", "universe", "splits"} <= config.documents.keys()
