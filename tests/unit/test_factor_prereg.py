"""Guard tests for the frozen factor-study pre-registration."""

from pathlib import Path
from typing import Any

import yaml


def load_factor_config() -> dict[str, Any]:
    """Load the frozen factor-study YAML directly."""
    loaded = yaml.safe_load(Path("config/factor.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_factor_config_has_no_owner_decision_placeholders() -> None:
    """Every owner slot must be committed before data access or study code."""
    config = load_factor_config()
    assert config["tuition_budget_inr"] == 0
    assert config["construction"]["basket_fraction"] == 0.2
    assert config["construction"]["rebalance_frequency"] == "monthly"
    assert config["construction"]["max_one_way_turnover_fraction"] == 0.5
    assert config["universe"]["size"] == 200

    hypotheses = config["hypotheses"]
    assert len(hypotheses) == 4
    for hypothesis in hypotheses:
        assert hypothesis["definition"]
        assert hypothesis["expected_sign"] == "positive"
        assert "[OWNER DECISION" not in hypothesis["definition"]


def test_factor_config_forbids_look_ahead() -> None:
    """Signals, orders, and fundamentals all require an explicit forward lag."""
    config = load_factor_config()
    assert config["construction"]["signal_to_order_lag_sessions"] >= 1
    assert config["fundamentals"]["announcement_lag_sessions"] >= 1
    assert config["fundamentals"]["restatement_leak_test_required"] is True
    assert config["universe"]["delisted_inclusive"] is True
    assert config["price"]["requires_pit_delisted_inclusive_prices"] is True


def test_preregistration_document_is_committed() -> None:
    """The frozen document may not retain draft placeholders."""
    text = Path("docs/FACTOR_STUDY_PREREG.md").read_text(encoding="utf-8")
    assert "Status: **COMMITTED" in text
    assert "[OWNER DECISION" not in text
    assert "₹0 tuition" in text
