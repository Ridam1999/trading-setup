"""Tests for the factor-study readiness gate and frozen configuration."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from nse_engine.cli import app
from nse_engine.factor.config import (
    FactorConfig,
    FactorDataConfig,
    load_factor_config,
)
from nse_engine.factor.readiness import (
    DISCLAIMER,
    assess_readiness,
    verify_preregistration,
)
from nse_engine.study.trials import TrialRegistry

CONFIG_DIR = Path("config")
PREREG_PATH = Path("docs/FACTOR_STUDY_PREREG.md")


def test_frozen_config_matches_committed_document() -> None:
    """The configured hash must match the committed pre-registration."""
    config = load_factor_config(CONFIG_DIR)
    assert verify_preregistration(PREREG_PATH, config.prereg_sha256)


def test_preregistration_tampering_is_detected(tmp_path: Path) -> None:
    """Editing the frozen document after commit blocks readiness."""
    config = load_factor_config(CONFIG_DIR)
    changed = tmp_path / "prereg.md"
    changed.write_text("tampered", encoding="utf-8")
    assert verify_preregistration(changed, config.prereg_sha256) is False


def test_factor_config_rejects_non_causal_lag() -> None:
    """A zero-session signal-to-order lag is invalid by construction."""
    payload = load_factor_config(CONFIG_DIR).model_dump()
    payload["construction"]["signal_to_order_lag_sessions"] = 0
    with pytest.raises(ValidationError):
        FactorConfig.model_validate(payload)


def test_missing_pit_inputs_block_readiness(tmp_path: Path) -> None:
    """The checked-in null data paths must block any factor execution."""
    config = load_factor_config(CONFIG_DIR)
    data = FactorDataConfig()
    report = assess_readiness(config, data, PREREG_PATH, TrialRegistry(tmp_path / "trials.sqlite"))
    assert report.ready is False
    assert {"pit_prices_dir", "announcements_path", "membership_path"}.issubset(
        set(report.blockers)
    )


def test_supplied_pit_inputs_pass_readiness(tmp_path: Path) -> None:
    """Ready checks pass only when every frozen requirement is satisfiable."""
    config = load_factor_config(CONFIG_DIR)
    prices = tmp_path / "prices"
    prices.mkdir()
    announcements = tmp_path / "announcements.csv"
    announcements.write_text("symbol,date,eps\n", encoding="utf-8")
    membership = tmp_path / "membership.csv"
    membership.write_text("date,symbol\n", encoding="utf-8")
    prereg = tmp_path / "prereg.md"
    prereg.write_bytes(PREREG_PATH.read_bytes())
    data = FactorDataConfig(
        pit_prices_dir=prices,
        announcements_path=announcements,
        membership_path=membership,
    )
    report = assess_readiness(config, data, prereg, TrialRegistry(tmp_path / "trials.sqlite"))
    assert report.ready is True
    assert report.blockers == ()


def test_factor_check_cli_blocks_without_pit_inputs() -> None:
    """The CLI reports blockers and the disclaimer instead of running a study."""
    result = CliRunner().invoke(app, ["factor", "check"])
    assert result.exit_code == 1
    assert "FACTOR STUDY BLOCKED" in result.output
    assert DISCLAIMER in result.output
